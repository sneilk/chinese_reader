"""Озвучка перевода: кэш на диске поверх провайдера.

Кэш здесь не оптимизация, а способ не платить дважды. Текст перевода
предложения не меняется, значит и его звучание не меняется тоже — а
перечитывать одну и ту же главу читатель будет, и не раз. Поэтому:

* **ключ — содержимое, а не идентификатор.** Хэш от текста вместе с голосом,
  интонацией и скоростью. Ключ по `chapter_id/idx` устарел бы в тот момент,
  когда глава переводится заново: файл остался бы на месте, а звучал бы старый
  текст — и заметить это можно только на слух;
* **кэш переживает базу.** Файлы лежат в `data/tts/` и не привязаны к
  таблицам: удалённая и заново загруженная глава озвучится из них бесплатно;
* **в журнал расходов пишется только новый синтез.** Иначе счёт рос бы от
  прослушиваний, а не от денег.

Запись атомарная — во временный файл рядом и переименование. Оборванный на
середине mp3 остался бы в кэше навсегда и играл бы обрывок: `rename` в
пределах одной файловой системы неделим, а «почти записанный файл» — нет.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Chapter, Sentence
from app.providers.speech import SpeechFailure, Synthesizer
from app.services import budget

log = logging.getLogger(__name__)

PROVIDER = "yandex"

_EXTENSIONS = {"audio/mpeg": ".mp3", "audio/ogg": ".ogg", "audio/x-pcm": ".pcm"}


def cache_path(signature: str, text: str, content_type: str) -> Path:
    """Путь к файлу озвучки. Каталог по первым двум знакам хэша: их 256.

    Раскладка по подкаталогам не украшение: за пару книг набирается несколько
    тысяч файлов, а каталог такого размера тормозит и `ls`, и бэкап.
    """
    digest = hashlib.sha256(f"{signature}|{text}".encode()).hexdigest()
    suffix = _EXTENSIONS.get(content_type, ".bin")
    return settings.tts_cache_dir / digest[:2] / f"{digest}{suffix}"


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.part")
    tmp.write_bytes(data)
    tmp.replace(path)


async def audio_for_sentence(
    session: Session,
    chapter: Chapter,
    sentence: Sentence,
    synthesizer: Synthesizer,
) -> tuple[Path, str]:
    """Файл с озвучкой перевода предложения. Синтезирует, только если его нет.

    Бросает `SpeechFailure`, если озвучивать нечего или провайдер отказал, и
    `budget.BudgetExceeded` — если упёрлись в свой месячный потолок.
    """
    text = (sentence.translation or "").strip()
    if not text:
        raise SpeechFailure("перевода этого предложения нет — озвучивать нечего")

    path = cache_path(synthesizer.signature, text, synthesizer.content_type)
    if path.exists():
        return path, synthesizer.content_type

    # До синтеза: потолок обязан останавливать отправку, а не отчитываться.
    budget.check_speech(session, len(text))

    result = await synthesizer.synthesize(text)
    _write_atomic(path, result.audio)
    budget.record_speech(
        session,
        provider=PROVIDER,
        voice=synthesizer.voice,
        chars_sent=result.chars_sent,
        chapter_id=chapter.id,
    )
    log.info("озвучено: глава %s, предложение %s -> %s", chapter.id, sentence.idx, path.name)
    return path, result.content_type


def _cached_files() -> list[tuple[float, int, Path]]:
    """Файлы кэша: (когда трогали, размер, путь).

    «Когда трогали» — максимум из времени доступа и времени записи. Одного
    `atime` мало: файловые системы монтируются с `relatime`, и у файла, который
    ни разу не слушали, он равен времени создания — то есть ведёт себя как
    `mtime`. Максимум делает величину монотонной в обоих случаях.
    """
    root = settings.tts_cache_dir
    if not root.exists():
        return []

    found: list[tuple[float, int, Path]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        stat = path.stat()
        found.append((max(stat.st_atime, stat.st_mtime), stat.st_size, path))
    return found


def cache_size_bytes() -> int:
    """Сколько места занял кэш озвучки. Для экрана состояния."""
    return sum(size for _, size, _ in _cached_files())


def prune_cache(max_bytes: int | None = None) -> tuple[int, int]:
    """Ужать кэш до потолка, выбрасывая давно не звучавшее. (файлов, байт).

    Кэш вечный по смыслу: текст перевода не меняется, значит и его звучание
    тоже, — но диск конечен, и книга в полтысячи глав озвучивается в гигабайты.
    Рядом на том же диске лежат база и семь её копий, поэтому расти без предела
    кэш не может.

    Выбрасывается самое давно не звучавшее, и это дешевле, чем кажется:
    выброшенный файл не потерян, а лишь стоит одного повторного синтеза — и
    только если к той главе вернутся. Возвращаются же обычно к недавним.

    Потолок не жёсткий: он проверяется в ночном обслуживании, а не при каждом
    синтезе. Обходить тысячи файлов ради одного нового mp3 значило бы платить
    за уборку чаще, чем за саму работу.
    """
    limit = settings.speech_cache_max_bytes if max_bytes is None else max_bytes
    files = _cached_files()
    total = sum(size for _, size, _ in files)
    if limit <= 0 or total <= limit:
        return 0, 0

    # Давно не звучавшие — первыми на выход.
    files.sort(key=lambda item: item[0])

    removed = freed = 0
    for _, size, path in files:
        if total - freed <= limit:
            break
        try:
            path.unlink()
        except OSError as e:  # noqa: PERF203 — уборка не должна падать из-за одного файла
            log.warning("не удалось убрать из кэша %s: %s", path.name, e)
            continue
        removed += 1
        freed += size

    log.info("кэш озвучки: убрано %s файлов, освобождено %s КБ", removed, freed // 1024)
    return removed, freed

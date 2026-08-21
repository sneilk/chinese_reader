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


def cache_size_bytes() -> int:
    """Сколько места занял кэш озвучки. Для экрана состояния."""
    root = settings.tts_cache_dir
    if not root.exists():
        return 0
    return sum(f.stat().st_size for f in root.rglob("*") if f.is_file())

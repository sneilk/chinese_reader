"""Импорт англо-русского словаря в dict_entries.

Формат тот же DSL, что у БКРС (T0.7), поэтому разбор берётся оттуда целиком —
`parse_dsl` не знает ни про иероглифы, ни про пиньинь, ему всё равно, что за
язык в заголовках. Свои здесь только три вещи, и все три языковые.

**Годный заголовок.** У БКРС это «есть иероглиф и не длиннее восьми знаков»;
здесь — латиница, апострофы и дефисы. Словосочетания отбрасываются: слово в
карточку приходит по одному, а `by the way` читатель выделить не может.

**Транскрипция вместо чтения.** В английских DSL она стоит первой строкой
карточки, обычно в `[t]…[/t]`. Общий стриппер снимает теги и оставляет её
первым «значением» — то есть карточка начиналась бы со строки вида `ˈrʌnɪŋ`
вместо перевода. Опознаём её тем же приёмом, каким БКРС опознаёт пиньинь:
в англо-русском словаре строка без единой кириллической буквы значением быть
не может. Уезжает она в ту же колонку `reading`, где у китайского пиньинь:
смысл один — «как это звучит».

**Заголовки складываются в нижний регистр.** Поиск идёт по форме из текста
(`services/lookup.py`), а она приходит с большой буквы в начале предложения.
Словарь, где `Run` и `run` — разные статьи, ответил бы на первую половину
попаданий «не найдено».

Дамп в репозиторий не кладётся: где его взять — в README, рядом с CC-CEDICT
и БКРС.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import DictEntry
from app.domain import Language
from app.services.bkrs_import import BkrsEntry, open_dump, parse_dsl

log = logging.getLogger(__name__)

SOURCE = "endict"

_CYRILLIC = re.compile(r"[а-яёА-ЯЁ]")
# Знаки МФА и скобки, в которых транскрипцию принято записывать. Одной только
# «латиницы без кириллицы» мало: под это подходит и английский пример
# употребления, который значением тоже не является, но и чтением не станет.
_TRANSCRIPTION_HINT = re.compile(r"[ˈˌəɪʊɔæʌθðŋʃʒːɑɒɜɛɡ]|^[\[/].*[\]/]$")

# Годный заголовок: латиница, внутри допустимы апостроф и дефис.
_HEADWORD_RE = re.compile(r"^[a-z]+(?:['\-][a-z]+)*$")

# Длиннее этого в тексте не выделяется: токен — одно слово.
MAX_HEADWORD_LEN = 32


def looks_like_transcription(text: str) -> bool:
    """Отличить транскрипцию от значения: `[ˈrʌnɪŋ]` против «бег»."""
    text = text.strip()
    if not text or _CYRILLIC.search(text):
        return False
    return bool(_TRANSCRIPTION_HINT.search(text))


def split_reading(entry: BkrsEntry) -> tuple[str | None, list[str]]:
    """Отделить транскрипцию от значений. Она стоит только первой строкой."""
    senses = [s for s in entry.senses if s.strip()]
    reading = entry.reading
    if senses and reading is None and looks_like_transcription(senses[0]):
        reading = senses[0].strip("[]/ ")
        senses = senses[1:]
    return reading, senses


def usable_headwords(entry: BkrsEntry) -> Iterator[str]:
    """Отобрать заголовки, годные для словаря: одно английское слово."""
    seen: set[str] = set()
    for headword in entry.headwords:
        word = headword.strip().lower()
        if word in seen or len(word) > MAX_HEADWORD_LEN or not _HEADWORD_RE.match(word):
            continue
        seen.add(word)
        yield word


def rows_from(entry: BkrsEntry) -> Iterator[dict]:
    """Строки для `dict_entries` из одной статьи DSL."""
    reading, senses = split_reading(entry)
    if not senses:
        return
    senses_json = json.dumps(senses, ensure_ascii=False)
    for headword in usable_headwords(entry):
        yield {
            "lang": Language.EN,
            "headword": headword,
            "reading": reading,
            "senses_json": senses_json,
            "source": SOURCE,
        }


def import_file(session: Session, path: Path, *, batch: int = 5000) -> int:
    """Залить дамп в dict_entries, заменив прошлый импорт того же источника."""
    session.execute(delete(DictEntry).where(DictEntry.source == SOURCE))
    session.commit()

    total = 0
    buf: list[dict] = []
    with open_dump(path) as fh:
        for entry in parse_dsl(fh):
            buf.extend(rows_from(entry))
            if len(buf) >= batch:
                session.bulk_insert_mappings(DictEntry, buf)
                session.commit()
                total += len(buf)
                buf.clear()

    if buf:
        session.bulk_insert_mappings(DictEntry, buf)
        session.commit()
        total += len(buf)

    log.info("импортировано статей англо-русского словаря: %s", total)
    return total

"""Импорт CC-CEDICT в dict_entries.

CC-CEDICT распространяется по CC BY-SA: атрибуция обязательна, она указана
в README. Сам дамп в git не кладётся — качается скриптом при настройке
(§2.2 концепции).

Формат строки:

    傳統 传统 [chuan2 tong3] /tradition/traditional/

то есть «традиционное упрощённое [чтение] /значение/значение/». Строки,
начинающиеся с `#`, — служебные.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import DictEntry
from app.lang.pinyin import numbered_to_accented

log = logging.getLogger(__name__)

CEDICT_URL = "https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz"
SOURCE = "cedict"

_LINE = re.compile(r"^(\S+)\s+(\S+)\s+\[([^\]]*)\]\s+/(.*)/\s*$")


@dataclass(frozen=True)
class CedictRow:
    traditional: str
    simplified: str
    reading_numbered: str
    senses: list[str]


def parse_line(line: str) -> CedictRow | None:
    """Разобрать строку дампа. `None` — комментарий или мусор."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    m = _LINE.match(line)
    if not m:
        return None
    traditional, simplified, reading, senses = m.groups()
    parts = [s.strip() for s in senses.split("/") if s.strip()]
    if not parts:
        return None
    return CedictRow(traditional, simplified, reading.strip(), parts)


def parse_stream(lines: Iterator[str]) -> Iterator[CedictRow]:
    for line in lines:
        row = parse_line(line)
        if row is not None:
            yield row


def download(dest: Path) -> Path:
    """Скачать дамп, если его ещё нет. Возвращает путь к распакованному файлу."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        log.info("дамп уже на месте: %s", dest)
        return dest

    gz = dest.with_suffix(dest.suffix + ".gz")
    log.info("качаю CC-CEDICT в %s", gz)
    urllib.request.urlretrieve(CEDICT_URL, gz)  # noqa: S310 — адрес фиксирован выше
    with gzip.open(gz, "rt", encoding="utf-8") as src, dest.open("w", encoding="utf-8") as out:
        out.writelines(src)
    gz.unlink()
    return dest


def import_file(session: Session, path: Path, *, batch: int = 5000) -> int:
    """Залить дамп в dict_entries, заменив прошлый импорт того же источника."""
    session.execute(delete(DictEntry).where(DictEntry.source == SOURCE))
    session.commit()

    total = 0
    buf: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for row in parse_stream(fh):
            buf.append(
                {
                    "lang": "zh",
                    "headword": row.simplified,
                    "traditional": row.traditional if row.traditional != row.simplified else None,
                    "reading": numbered_to_accented(row.reading_numbered),
                    "reading_numbered": row.reading_numbered,
                    "senses_json": json.dumps(row.senses, ensure_ascii=False),
                    "source": SOURCE,
                }
            )
            if len(buf) >= batch:
                session.bulk_insert_mappings(DictEntry, buf)
                session.commit()
                total += len(buf)
                buf.clear()

    if buf:
        session.bulk_insert_mappings(DictEntry, buf)
        session.commit()
        total += len(buf)

    log.info("импортировано статей: %s", total)
    return total

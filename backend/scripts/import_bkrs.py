"""Импорт БКРС: PYTHONPATH=. python scripts/import_bkrs.py [ГГММДД]

Дамп — ежедневная выгрузка bkrs.info одним файлом (~83 МБ в архиве). В git он
не кладётся: это чужая база, и качать её должен тот, кто ставит сервис.

Без аргумента берётся вчерашняя дата: сегодняшняя выгрузка появляется не с
началом суток, и запрос за неё чаще всего даёт 404.

После импорта пересобирается userdict: словарь и сегментатор обязаны говорить
об одном и том же (segmentation.md §2).
"""

import logging
import sys
from datetime import UTC, datetime, timedelta

from app.config import settings
from app.db.session import SessionLocal
from app.lang.segment import build_userdict
from app.services.bkrs_import import download, import_file

logging.basicConfig(level=logging.INFO, format="%(message)s")

date = (
    sys.argv[1] if len(sys.argv) > 1 else (datetime.now(UTC) - timedelta(days=1)).strftime("%y%m%d")
)

path = download(settings.data_dir / f"bkrs-{date}.dsl", date)
with SessionLocal() as session:
    total = import_file(session, path)
    words = build_userdict(session, settings.userdict_path)

print(f"статей БКРС: {total}, слов в userdict: {words}")

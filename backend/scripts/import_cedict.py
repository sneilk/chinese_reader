"""Разовый импорт CC-CEDICT: PYTHONPATH=. python scripts/import_cedict.py

Импорт и сборка userdict — одна операция намеренно: после замены словаря
файл для jieba устаревает, а разъехавшиеся словарь и сегментатор дают ровно
те перекосы, о которых segmentation.md §2.
"""

import logging
import sys

from app.config import settings
from app.db.session import SessionLocal
from app.lang.segment import build_userdict
from app.services.dict_import import download, import_file

logging.basicConfig(level=logging.INFO, format="%(message)s")

path = download(settings.data_dir / "cedict.txt")
with SessionLocal() as session:
    total = import_file(session, path)
    words = build_userdict(session, settings.userdict_path)
print(f"статей в базе: {total}, слов в userdict: {words}")
sys.exit(0)

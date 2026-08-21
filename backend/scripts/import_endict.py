"""Импорт англо-русского словаря: PYTHONPATH=. python scripts/import_endict.py <файл.dsl>

Путь к дампу — аргументом, без автоскачивания. У CC-CEDICT и БКРС адреса
выгрузок постоянные и объявленные, поэтому там скрипт качает сам; у
англо-русских словарей единого такого адреса нет, и зашитая ссылка означала
бы обещание, которое сломается тихо.

Формат — DSL (тот же, что у БКРС), распакованный или в `.gz`.

userdict здесь не пересобирается, и это не забывчивость: userdict нужен jieba,
то есть только китайскому. Английский режется регуляркой и словаря для резки
не требует вовсе (`lang/segment_en.py`).
"""

import logging
import sys
from pathlib import Path

from app.db.session import SessionLocal
from app.services.endict_import import import_file

logging.basicConfig(level=logging.INFO, format="%(message)s")

if len(sys.argv) != 2:
    print(__doc__.splitlines()[0])
    sys.exit(2)

path = Path(sys.argv[1])
if not path.exists():
    print(f"файла нет: {path}")
    sys.exit(1)

with SessionLocal() as session:
    total = import_file(session, path)
print(f"статей англо-русского словаря в базе: {total}")
sys.exit(0)

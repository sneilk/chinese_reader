"""Импорт CC-CEDICT.

Строки для тестов написаны от руки в формате дампа: сам дамп в git не лежит
(9,7 МБ, качается скриптом), а привязывать тесты к его содержимому значило бы
ломать их при каждом обновлении словаря.
"""

import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import DictEntry
from app.services.dict_import import SOURCE, import_file, parse_line

LINES = """\
# CC-CEDICT
# заголовок дампа, такие строки пропускаются
傳統 传统 [chuan2 tong3] /tradition/traditional/
朋友 朋友 [peng2 you5] /friend/CL:個|个[ge4],位[wei4]/
北京 北京 [Bei3 jing1] /Beijing, capital of China/
"""


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def dump(tmp_path):
    path = tmp_path / "cedict.txt"
    path.write_text(LINES, encoding="utf-8")
    return path


def test_parse_line():
    row = parse_line("傳統 传统 [chuan2 tong3] /tradition/traditional/")
    assert row.traditional == "傳統"
    assert row.simplified == "传统"
    assert row.reading_numbered == "chuan2 tong3"
    assert row.senses == ["tradition", "traditional"]


def test_parse_comment_and_junk():
    assert parse_line("# CC-CEDICT") is None
    assert parse_line("") is None
    assert parse_line("это не строка дампа") is None


def test_parse_line_without_senses():
    """Заголовок без значений бесполезен: карточке нечего показать."""
    assert parse_line("传统 传统 [chuan2 tong3] //") is None


def test_parse_keeps_slashes_inside_senses():
    """Внутри значения есть отсылки вида CL:個|个[ge4] — резать их нельзя."""
    row = parse_line("朋友 朋友 [peng2 you5] /friend/CL:個|个[ge4],位[wei4]/")
    assert row.senses == ["friend", "CL:個|个[ge4],位[wei4]"]


def test_import_writes_entries(session, dump):
    assert import_file(session, dump) == 3

    entry = session.scalars(select(DictEntry).where(DictEntry.headword == "传统")).one()
    assert entry.traditional == "傳統"
    assert entry.reading == "chuán tǒng"  # диакритика считается на импорте
    assert entry.reading_numbered == "chuan2 tong3"
    assert json.loads(entry.senses_json) == ["tradition", "traditional"]
    assert entry.source == SOURCE
    assert entry.lang == "zh"


def test_traditional_null_when_same(session, dump):
    """Хранить копию заголовка смысла нет: у большинства статей формы совпадают."""
    import_file(session, dump)
    entry = session.scalars(select(DictEntry).where(DictEntry.headword == "朋友")).one()
    assert entry.traditional is None


def test_reimport_replaces_not_duplicates(session, dump):
    """Повторный импорт — обычное дело: дамп обновляется, статьи не должны двоиться."""
    import_file(session, dump)
    import_file(session, dump)
    assert session.query(DictEntry).count() == 3


def test_reimport_keeps_other_sources(session, dump):
    """БКРС (T2.2) приедет вторым источником и переживать импорт CC-CEDICT обязан."""
    session.add(DictEntry(headword="传统", senses_json="[]", source="bkrs"))
    session.commit()
    import_file(session, dump)
    assert session.query(DictEntry).filter_by(source="bkrs").count() == 1
    assert session.query(DictEntry).count() == 4


def test_import_batching(session, dump):
    """Дамп в 124 тысячи статей заливается пачками; на границе ничего не теряется."""
    assert import_file(session, dump, batch=2) == 3

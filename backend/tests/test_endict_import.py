"""Импорт англо-русского словаря из DSL.

Дамп в git не лежит, поэтому фикстура синтетическая и покрывает не «весь
словарь», а те места, где разбор может ошибиться молча: транскрипция вместо
значения, несколько заголовков на одну карточку, словосочетание среди слов,
заголовок с большой буквы.

Разбор самого DSL здесь не проверяется — он общий с БКРС и покрыт там
(`test_bkrs_import.py`). Проверяется английская часть поверх него.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models import DictEntry
from app.services.bkrs_import import BkrsEntry, parse_dsl
from app.services.endict_import import (
    SOURCE,
    import_file,
    looks_like_transcription,
    rows_from,
    split_reading,
    usable_headwords,
)

DUMP = Path(__file__).parent / "data" / "endict-synthetic.dsl"


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'dict.db'}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, class_=Session, expire_on_commit=False)() as s:
        yield s


@pytest.fixture
def imported(session):
    import_file(session, DUMP)
    return session


def entry(session, headword) -> DictEntry | None:
    return session.scalars(
        select(DictEntry).where(DictEntry.headword == headword, DictEntry.source == SOURCE)
    ).first()


def senses(session, headword) -> list[str]:
    row = entry(session, headword)
    return json.loads(row.senses_json) if row else []


# --- транскрипция ---


@pytest.mark.parametrize("text", ["rʌn", "ˈlæntən", "[glɑːs]", "siː"])
def test_transcription_recognised(text):
    assert looks_like_transcription(text)


@pytest.mark.parametrize("text", ["бежать, бегать", "город", "", "shop"])
def test_translation_is_not_a_transcription(text):
    """Строка с кириллицей — значение. Латиница без знаков МФА — тоже не чтение."""
    assert not looks_like_transcription(text)


def test_transcription_leaves_the_senses(imported):
    """Иначе карточка начиналась бы со строки `rʌn` вместо перевода."""
    assert senses(imported, "run")[0] == "бежать, бегать"
    assert entry(imported, "run").reading == "rʌn"


def test_reading_brackets_are_stripped():
    got = split_reading(BkrsEntry(headwords=["glass"], senses=["[glɑːs]", "стекло"]))
    assert got == ("glɑːs", ["стекло"])


def test_entry_without_senses_is_skipped():
    """Одна транскрипция и ничего больше — показывать нечего."""
    assert list(rows_from(BkrsEntry(headwords=["ghost"], senses=["ɡəʊst"]))) == []


# --- заголовки ---


def test_headwords_are_lowercased():
    """Поиск идёт по форме из текста, а она бывает с большой буквы."""
    assert list(usable_headwords(BkrsEntry(headwords=["Run"], senses=["бежать"]))) == ["run"]


def test_phrases_are_dropped(imported):
    """Словосочетание в тексте не выделяется: токен — одно слово."""
    assert entry(imported, "by the way") is None


def test_several_headwords_share_one_card(imported):
    """`colour` и `color` — варианты написания одной статьи, а не две статьи."""
    assert senses(imported, "colour") == senses(imported, "color") == ["цвет, окраска"]


def test_duplicate_headwords_are_collapsed():
    got = list(usable_headwords(BkrsEntry(headwords=["run", "Run"], senses=["бежать"])))
    assert got == ["run"]


# --- импорт целиком ---


def test_import_counts_rows(imported):
    rows = imported.scalars(select(DictEntry).where(DictEntry.source == SOURCE)).all()
    # Девять годных заголовков: `by the way` отброшено, у цвета их два.
    assert len(rows) == 10
    assert all(r.lang == "en" for r in rows)


def test_examples_are_dropped(imported):
    """`[ex]` в карточке занимает больше места, чем значения."""
    assert all("he runs a shop" not in s for s in senses(imported, "run"))


def test_marks_are_dropped(imported):
    """Помета `[p]перен.[/p]` — не часть значения."""
    assert "перен." not in " ".join(senses(imported, "wolf"))


def test_reimport_replaces_previous(session):
    import_file(session, DUMP)
    first = session.scalars(select(DictEntry).where(DictEntry.source == SOURCE)).all()
    import_file(session, DUMP)
    second = session.scalars(select(DictEntry).where(DictEntry.source == SOURCE)).all()
    assert len(first) == len(second), "повторный импорт не должен задваивать словарь"


def test_directives_are_not_entries(imported):
    assert entry(imported, "#name") is None
    assert entry(imported, "#index_language") is None


def test_parse_dsl_is_shared_with_bkrs():
    """Английский импорт не переписывает разбор DSL, а пользуется общим."""
    with DUMP.open(encoding="utf-8-sig") as fh:
        heads = [e.headwords[0] for e in parse_dsl(fh)]
    assert "run" in heads and "lantern" in heads

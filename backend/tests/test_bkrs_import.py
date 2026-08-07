"""Импорт БКРС из DSL.

Фикстура синтетическая и написана специально под ловушки формата: сам дамп в
git не кладётся (83 МБ и чужая база), а привязывать тесты к его содержимому
значит ломать их при каждом обновлении словаря.

Форма фикстуры сверена с живой выборкой T0.7 на ВМ. Именно та сверка и
показала, что чтение лежит отдельной строкой после заголовка, а не в `[p]`:
до неё парсер не находил пиньинь ни у одной статьи из двадцати семи.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import DictEntry
from app.services.bkrs_import import (
    SOURCE,
    import_file,
    looks_like_pinyin,
    parse_dsl,
    strip_markup,
)

FIXTURE = Path(__file__).parent / "data" / "bkrs-synthetic.dsl"


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'bkrs.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def entries():
    with FIXTURE.open(encoding="utf-8") as fh:
        return list(parse_dsl(fh))


def by_headword(entries, headword):
    return next(e for e in entries if headword in e.headwords)


# --- стриппер разметки ---


def test_strip_keeps_text_drops_tags():
    assert strip_markup("[m1][c]окно[/c][/m]") == "окно"


def test_examples_are_dropped_with_content():
    """[ex] и [*] занимают в карточке больше места, чем значения."""
    assert strip_markup("[m1][ex]他在学习 он учится[/ex][/m]") == ""
    assert strip_markup("[m1]значение[/m][m1][*]пример[/*][/m]") == "значение"


def test_marks_are_dropped():
    assert strip_markup("[m1][p]гл.[/p]учиться[/m]") == "учиться"


def test_reference_becomes_word():
    assert strip_markup("см. <<帮助>>") == "см. 帮助"


def test_escaped_brackets_survive():
    """`\\[` — это скобка в тексте, а не начало тега.

    Без защиты весь кусок `\\[в тексте\\]` считается тегом и исчезает вместе
    с содержимым — то есть из статьи молча пропадает часть значения.
    """
    assert strip_markup(r"[m1]значение \[в тексте\][/m]") == "значение [в тексте]"


@pytest.mark.parametrize("text", ["chuāng hu", "yi1 jian4", "hǎo"])
def test_pinyin_recognised(text):
    assert looks_like_pinyin(text)


@pytest.mark.parametrize("text", ["гл.", "сущ.", "чэнъюй", "学习", ""])
def test_marks_are_not_pinyin(text):
    assert not looks_like_pinyin(text)


# --- разбор статей ---


def test_parses_all_usable_entries(entries):
    headwords = [h for e in entries for h in e.headwords]
    assert "学习" in headwords
    assert "窗户" in headwords
    assert "一见如故" in headwords


def test_senses_collected_without_examples(entries):
    entry = by_headword(entries, "学习")
    assert entry.senses == ["1) учиться, заниматься", "2) осваивать, изучать"]


def test_reading_taken_from_its_own_line(entries):
    """Чтение — отдельная строка сразу после заголовка, а не содержимое [p].

    Выяснилось на живой фикстуре T0.7: пиньинь не нашёлся ни у одной из
    двадцати семи статей, хотя в файле его восемьдесят девять слогов.
    """
    assert by_headword(entries, "窗户").reading == "chuāng hu"
    assert by_headword(entries, "学习").reading == "xué xí"


def test_part_of_speech_is_not_a_reading(entries):
    """`гл.` — помета: она уходит вместе с [p] и в чтение не попадает."""
    assert "гл." not in (by_headword(entries, "学习").senses or [""])[0]


def test_entry_without_reading(entries):
    """Чтение есть не у каждой статьи, и это нормально."""
    assert by_headword(entries, "一见如故").reading is None
    assert by_headword(entries, "一见如故").senses == ["сойтись с первой встречи"]


def test_variant_headwords_share_one_card(entries):
    """Несколько заголовков подряд — варианты написания одной статьи."""
    entry = by_headword(entries, "书")
    assert entry.headwords == ["書", "书"]
    assert entry.senses == ["книга"]
    assert entry.reading == "shū"


def test_bom_does_not_become_an_entry():
    """BOM в начале файла: без защиты первая директива станет заголовком."""
    lines = ['\ufeff#NAME "словарь"', "学习", "\t[m1]учиться[/m]"]
    entries = list(parse_dsl(lines))
    assert [h for e in entries for h in e.headwords] == ["学习"]


def test_entry_without_body_is_skipped(entries):
    """Заголовок в конце файла без тела — это обрыв дампа, а не статья.

    Внутри файла такой случай неотличим от вариантов написания: там заголовки
    тоже идут подряд без тела между ними. Поэтому отбрасывается только тот,
    у которого значений не появилось вовсе.
    """
    assert all("断" not in e.headwords for e in entries)


def test_service_header_skipped(entries):
    assert all(not h.startswith("#") for e in entries for h in e.headwords)


def test_inline_formatting_unwrapped(entries):
    assert by_headword(entries, "帮忙").senses[0] == "помогать; выручать"


def test_escaped_bracket_survives_import(entries):
    assert by_headword(entries, "括号").senses == ["значение со скобкой [в тексте]"]


# --- импорт ---


def test_import_writes_entries(session):
    total = import_file(session, FIXTURE)
    assert total > 0

    entry = session.scalars(select(DictEntry).where(DictEntry.headword == "窗户")).one()
    assert entry.source == SOURCE
    assert entry.reading == "chuāng hu"
    assert json.loads(entry.senses_json) == ["окно"]


def test_variants_become_separate_rows(session):
    """Искать будут по обоим написаниям, значит и строк должно быть две."""
    import_file(session, FIXTURE)
    rows = session.scalars(select(DictEntry).where(DictEntry.headword.in_(["書", "书"]))).all()
    assert len(rows) == 2
    assert {json.loads(r.senses_json)[0] for r in rows} == {"книга"}


def test_reimport_replaces_not_duplicates(session):
    first = import_file(session, FIXTURE)
    second = import_file(session, FIXTURE)
    assert first == second
    assert session.query(DictEntry).filter_by(source=SOURCE).count() == first


def test_reimport_keeps_cedict(session):
    """CC-CEDICT приехал раньше и переживать импорт БКРС обязан."""
    session.add(DictEntry(headword="窗户", senses_json="[]", source="cedict"))
    session.commit()
    import_file(session, FIXTURE)
    assert session.query(DictEntry).filter_by(source="cedict").count() == 1

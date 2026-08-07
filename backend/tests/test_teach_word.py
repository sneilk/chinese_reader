"""Правка границ учит сегментатор (T2.7).

Критерий задачи — «поправленное имя героя дальше режется правильно в следующей
главе», поэтому проверяется не факт записи в базу, а разметка до и после. И
отдельно то, что правка переживает перезапуск: слово, живущее только в памяти
процесса, исчезнет при первом же деплое.

Пример везде один — 张仙姑, имя героини из главы-фикстуры. jieba режет его на
张 и 仙姑, и это ровно тот случай, ради которого §5 segmentation.md и написан.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_optional_segmenter, get_session_factory
from app.db.base import Base
from app.db.models import DictEntry, UserWord
from app.lang.segment import (
    CEDICT_FREQ,
    USER_FREQ,
    Segmenter,
    build_userdict,
    is_teachable,
    teach_word,
)
from app.main import app

NAME = "张仙姑"
SENTENCE = f"{NAME}走了进来。"


@pytest.fixture(scope="module")
def plain() -> Segmenter:
    return Segmenter()


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'teach.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def surfaces(segmenter: Segmenter, text: str = SENTENCE) -> list[str]:
    return [t.surface for t in segmenter.segment(text)]


def test_jieba_splits_the_name(plain):
    """Исходное состояние: без правки имя рассыпается."""
    assert NAME not in surfaces(plain)


def test_teaching_makes_it_whole(tmp_path):
    segmenter = Segmenter()
    assert teach_word(segmenter, tmp_path / "userdict.txt", NAME)
    assert NAME in surfaces(segmenter)


def test_teaching_survives_restart(tmp_path):
    """Слово, живущее только в памяти, исчезнет при первом же перезапуске."""
    path = tmp_path / "userdict.txt"
    teach_word(Segmenter(), path, NAME)

    # Новый процесс: сегментатор поднимается с файла и ничего не знает о старом.
    restarted = Segmenter(path)
    assert NAME in surfaces(restarted)


@pytest.mark.parametrize("word", ["好", "OK", "12", "一二三四五六七八九"])
def test_unteachable_words_rejected(tmp_path, word):
    """Односимвольные jieba и так режет по знаку, латиница ему ни к чему."""
    assert not is_teachable(word)
    assert not teach_word(None, tmp_path / "userdict.txt", word)
    assert not (tmp_path / "userdict.txt").exists()


def test_build_userdict_keeps_user_words(factory, tmp_path):
    """Пересборка файла из словаря не должна терять правки читателя."""
    with factory() as session:
        session.add(DictEntry(headword="窗户", senses_json="[]", source="cedict"))
        session.add(UserWord(lang="zh", headword=NAME))
        session.commit()

        path = tmp_path / "userdict.txt"
        build_userdict(session, path)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert f"{NAME} {USER_FREQ}" in lines
    assert Segmenter(path).segment(SENTENCE)[0].surface == NAME


def test_user_word_wins_over_dictionary_entry(factory, tmp_path):
    """Одно слово с двух сторон: базовая частота словаря и высокая — читателя.

    jieba берёт последнюю строку файла, поэтому слова читателя пишутся после
    словарных. Иначе его правка границ проиграла бы нашей же тройке.
    """
    with factory() as session:
        session.add(DictEntry(headword=NAME, senses_json="[]", source="cedict"))
        session.add(UserWord(lang="zh", headword=NAME))
        session.commit()
        path = tmp_path / "userdict.txt"
        build_userdict(session, path)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines == [f"{NAME} {CEDICT_FREQ}", f"{NAME} {USER_FREQ}"]
    assert NAME in surfaces(Segmenter(path))


def test_big_dictionary_does_not_reach_userdict(factory, tmp_path):
    """Словарь для карточки и словарь для резки — разные вещи.

    БКРС содержит почти любое сочетание из двух-четырёх знаков. Попади его
    заголовки в userdict с базовой частотой, текст рассыпается на куски:
    замерено на золотом наборе — 23 предложения из 25 меняют разметку. Здесь
    проверяется, что источник в userdict не попадает вовсе.
    """
    with factory() as session:
        session.add_all(
            [
                # jieba этого слова не знает, поэтому оно в файл попадёт.
                DictEntry(headword="短命鬼", senses_json="[]", source="cedict"),
                DictEntry(headword="黑得像几", senses_json="[]", source="bkrs"),
                DictEntry(headword="百年没擦", senses_json="[]", source="bkrs"),
            ]
        )
        session.commit()
        path = tmp_path / "userdict.txt"
        build_userdict(session, path)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines == [f"短命鬼 {CEDICT_FREQ}"]


def test_golden_set_survives_dictionary_import(factory, tmp_path):
    """Регресс-барьер: импорт словаря не должен менять разметку главы.

    Если он её меняет, читателю это видно как «ридер стал хуже резать», и
    искать причину он будет в сегментаторе, а не в импортёре словаря.
    """
    golden = (Path(__file__).parent / "data" / "segment-golden.txt").read_text(encoding="utf-8")
    lines = golden.splitlines()

    with factory() as session:
        # Большой словарь приносит все мыслимые сочетания из текста главы.
        han = re.compile(r"^[一-鿿]+$")
        for line in lines:
            text = line.replace("|", "")
            for n in (2, 3, 4):
                for i in range(len(text) - n + 1):
                    piece = text[i : i + n]
                    if han.match(piece):
                        session.add(DictEntry(headword=piece, senses_json="[]", source="bkrs"))
        session.commit()

        path = tmp_path / "userdict.txt"
        build_userdict(session, path)

    # Файл достраиваем поверх боевой вырезки — так же, как в бою.
    base = (Path(__file__).parent / "data" / "segment-userdict.txt").read_text(encoding="utf-8")
    merged = tmp_path / "merged.txt"
    merged.write_text(base + path.read_text(encoding="utf-8"), encoding="utf-8")

    segmenter = Segmenter(merged)
    for line in lines:
        text = line.replace("|", "")
        assert "|".join(t.surface for t in segmenter.segment(text)) == line, text


# --- через API ---


def test_saving_word_teaches_segmenter(factory, tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    segmenter = Segmenter()
    assert NAME not in surfaces(segmenter)

    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_optional_segmenter] = lambda: segmenter
    try:
        client = TestClient(app)
        r = client.post("/api/words", json={"headword": NAME, "reading": "zhāng xiān gū"})
        assert r.status_code == 201
    finally:
        app.dependency_overrides.clear()

    assert NAME in surfaces(segmenter), "живой сегментатор должен научиться сразу"
    assert f"{NAME} {USER_FREQ}" in (tmp_path / "userdict.txt").read_text(encoding="utf-8")


def test_resaving_does_not_duplicate_userdict_line(factory, tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_optional_segmenter] = lambda: Segmenter()
    try:
        client = TestClient(app)
        client.post("/api/words", json={"headword": NAME})
        client.post("/api/words", json={"headword": NAME})
    finally:
        app.dependency_overrides.clear()

    lines = (tmp_path / "userdict.txt").read_text(encoding="utf-8").splitlines()
    assert lines.count(f"{NAME} {USER_FREQ}") == 1


def test_word_is_saved_even_without_segmenter(factory, tmp_path, monkeypatch):
    """Правку читателя нельзя терять из-за того, что сегментатор не поднялся."""
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_optional_segmenter] = lambda: None
    try:
        client = TestClient(app)
        assert client.post("/api/words", json={"headword": NAME}).status_code == 201
    finally:
        app.dependency_overrides.clear()

    with factory() as session:
        assert session.query(UserWord).count() == 1

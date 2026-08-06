"""Поиск слова в словарях и ручка `GET /api/lookup`.

Статьи заводятся руками: настоящий дамп в git не лежит, а привязывать тесты к
его содержимому значит ломать их при каждом обновлении словаря.

Главный проверяемый случай — не «слово нашлось», а имя героя, которого в
словаре нет и не будет. Именно оно определяет, будет ли карточка полезной.
"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_session_factory
from app.db.base import Base
from app.db.models import DictEntry
from app.main import app
from app.services.lookup import is_dictionary_headword, lookup


def _entry(headword, senses, source="cedict", reading=None):
    return DictEntry(
        lang="zh",
        headword=headword,
        reading=reading,
        senses_json=json.dumps(senses, ensure_ascii=False),
        source=source,
    )


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'dict.db'}")
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    with maker() as session:
        session.add_all(
            [
                _entry("学习", ["to learn", "to study"], reading="xué xí"),
                _entry("学习", ["учиться", "изучать"], source="bkrs", reading="xué xí"),
                _entry("张", ["surname Zhang", "to open"], reading="zhāng"),
                _entry("仙", ["immortal", "celestial being"], reading="xiān"),
                _entry("姑", ["aunt", "girl"], reading="gū"),
                _entry("OK", ["okay"], reading=None),
            ]
        )
        session.commit()
    return maker


@pytest.fixture
def session(factory):
    with factory() as s:
        yield s


@pytest.fixture
def client(factory):
    app.dependency_overrides[get_session_factory] = lambda: factory
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_known_word(session):
    result = lookup(session, "学习")
    assert result.found
    assert not result.approximate
    assert [e.source for e in result.entries] == ["bkrs", "cedict"]


def test_russian_source_goes_first(session):
    """Русское значение читателю полезнее английского, английское — запасное."""
    result = lookup(session, "学习")
    assert result.entries[0].senses == ["учиться", "изучать"]


def test_missing_name_falls_back_to_characters(session):
    """Имя героя веб-новеллы не найдётся никогда — карточка собирается из знаков."""
    result = lookup(session, "张仙姑")

    assert not result.found
    assert result.approximate
    assert [c.char for c in result.chars] == ["张", "仙", "姑"]
    assert result.chars[0].reading == "zhāng"
    assert result.chars[1].senses == ["immortal", "celestial being"]


def test_unknown_character_still_listed(session):
    """Знак без статьи остаётся в карточке пустым: пропуск сбил бы порядок знаков."""
    result = lookup(session, "张兲")
    assert [c.char for c in result.chars] == ["张", "兲"]
    assert result.chars[1].senses == []


def test_latin_word_is_not_split(session):
    """Разбирать латиницу по буквам бессмысленно."""
    result = lookup(session, "unknown")
    assert not result.found
    assert result.chars == []


def test_empty_word(session):
    assert lookup(session, "  ").entries == []


def test_headword_filter():
    """Фильтр выдачи: иероглифы и не длиннее четырёх знаков."""
    assert is_dictionary_headword("学习")
    assert is_dictionary_headword("一见如故")
    assert not is_dictionary_headword("一见如故了")  # длинная фраза, не заголовок
    assert not is_dictionary_headword("OK")
    assert not is_dictionary_headword("")


# --- ручка ---


def test_api_known_word(client):
    r = client.get("/api/lookup", params={"word": "学习"})
    assert r.status_code == 200
    body = r.json()
    assert body["found"] is True
    assert body["approximate"] is False
    assert body["entries"][0]["source"] == "bkrs"
    assert body["entries"][0]["reading"] == "xué xí"


def test_api_name_fallback(client):
    body = client.get("/api/lookup", params={"word": "张仙姑"}).json()
    assert body["found"] is False
    assert body["approximate"] is True
    assert [c["char"] for c in body["chars"]] == ["张", "仙", "姑"]


def test_api_nothing_found(client):
    body = client.get("/api/lookup", params={"word": "zzz"}).json()
    assert body["found"] is False
    assert body["approximate"] is False
    assert body["entries"] == [] and body["chars"] == []


def test_api_requires_word(client):
    r = client.get("/api/lookup")
    assert r.status_code == 422
    assert r.json()["error"]["kind"] == "bad_request"

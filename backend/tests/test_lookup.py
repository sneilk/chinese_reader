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


def _entry(headword, senses, source="cedict", reading=None, lang="zh"):
    return DictEntry(
        lang=lang,
        headword=headword,
        reading=reading,
        senses_json=json.dumps(senses, ensure_ascii=False),
        source=source,
    )


def _en(headword, senses, reading=None):
    """Статья англо-русского словаря. Заголовки в нём строчные — как при импорте."""
    return _entry(headword, senses, source="endict", reading=reading, lang="en")


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
                _en("run", ["бежать, бегать", "управлять"], reading="rʌn"),
                _en("lantern", ["фонарь"], reading="ˈlæntən"),
                _en("wolf", ["волк"]),
                _en("city", ["город"]),
                _en("saw", ["пила"]),
                _en("see", ["видеть, смотреть"]),
                _en("do", ["делать, выполнять"]),
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


# --- английский ---
#
# У китайского беда в том, что слова нет в словаре (имя героя), и лечится она
# разбором по знакам. У английского обратная: слово есть, но стоит не в той
# форме. Поэтому здесь проверяется перебор форм — и то, что найденная форма
# доезжает наружу: карточка про `run` при `running` в тексте не должна
# выглядеть так, будто в словаре стоит ровно то, что в тексте.


def test_en_exact_word(session):
    result = lookup(session, "lantern", "en")
    assert result.found
    assert result.matched is None
    assert result.entries[0].senses == ["фонарь"]


def test_en_inflected_form_found_by_lemma(session):
    result = lookup(session, "running", "en")
    assert result.found
    assert result.matched == "run"
    assert result.entries[0].senses[0] == "бежать, бегать"


def test_en_irregular_plural(session):
    assert lookup(session, "wolves", "en").matched == "wolf"


def test_en_capitalised_word_found(session):
    """Слово в начале предложения приходит с большой буквы, а словарь строчный."""
    result = lookup(session, "City", "en")
    assert result.found
    assert result.matched == "city"


def test_en_exact_form_beats_the_lemma(session):
    """`saw` — и пила, и прошедшее от `see`. В тексте стоит пила."""
    result = lookup(session, "saw", "en")
    assert result.matched is None
    assert result.entries[0].senses == ["пила"]


def test_en_contraction(session):
    assert lookup(session, "don't", "en").matched == "do"


def test_en_no_character_fallback(session):
    """Разбирать английское слово по буквам бессмысленно — буква ничего не значит."""
    result = lookup(session, "zzzqqq", "en")
    assert not result.found
    assert not result.approximate
    assert result.chars == []


def test_en_does_not_see_chinese_entries(session):
    """Словари разделены по `lang`: китайская статья в английскую выдачу не течёт."""
    assert not lookup(session, "学习", "en").found


def test_zh_does_not_see_english_entries(session):
    assert not lookup(session, "lantern").found


def test_api_english_lookup(client):
    body = client.get("/api/lookup", params={"word": "wolves", "lang": "en"}).json()
    assert body["found"] is True
    assert body["matched"] == "wolf"
    assert body["entries"][0]["senses"] == ["волк"]


def test_api_rejects_unknown_language(client):
    """Языков ровно два, и оба — языки оригинала. `ru` читать не нужно."""
    r = client.get("/api/lookup", params={"word": "wolf", "lang": "ru"})
    assert r.status_code == 422
    assert r.json()["error"]["kind"] == "bad_request"

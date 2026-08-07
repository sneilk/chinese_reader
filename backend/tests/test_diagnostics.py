"""Ручка состояния сервиса.

Проверяется то, ради чего она существует: по ответу видно, что именно не
настроено. И отдельно — что в ответ не просочился ключ переводчика.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_session_factory
from app.config import settings
from app.db.base import Base
from app.db.models import Chapter, DictEntry, Document, Source, UserWord
from app.domain import ChapterStatus
from app.main import app


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'diag.db'}")
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    with maker() as session:
        chapter = Chapter(
            document=Document(source=Source(kind="web", site="example.com")),
            url="https://example.com/1.html",
            status=ChapterStatus.READY,
        )
        session.add_all(
            [
                chapter,
                DictEntry(headword="学习", senses_json="[]", source="cedict"),
                DictEntry(headword="学习", senses_json="[]", source="bkrs"),
                UserWord(lang="zh", headword="张仙姑"),
            ]
        )
        session.commit()
    return maker


@pytest.fixture
def client(factory):
    app.dependency_overrides[get_session_factory] = lambda: factory
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_counts(client):
    body = client.get("/api/diagnostics").json()

    assert body["chapters"] == 1
    assert body["user_words"] == 1
    assert body["dict_entries"] == 2
    assert body["dict_sources"] == {"cedict": 1, "bkrs": 1}


def test_shows_what_is_not_configured(client, monkeypatch):
    """Ключа нет — и это должно быть видно, а не выясняться по пустым переводам."""
    monkeypatch.setattr(settings, "yc_translate_api_key", "")
    body = client.get("/api/diagnostics").json()
    assert body["translator_configured"] is False

    monkeypatch.setattr(settings, "yc_translate_api_key", "key")
    monkeypatch.setattr(settings, "yc_folder_id", "folder")
    assert client.get("/api/diagnostics").json()["translator_configured"] is True


def test_does_not_leak_secrets(client, monkeypatch):
    monkeypatch.setattr(settings, "yc_translate_api_key", "секретный-ключ")
    monkeypatch.setattr(settings, "yc_folder_id", "каталог-облака")

    raw = client.get("/api/diagnostics").text

    assert "секретный-ключ" not in raw
    assert "каталог-облака" not in raw


def test_userdict_counted(client):
    """Словарь без userdict — частая поломка: сегментатор режет по-своему."""
    body = client.get("/api/diagnostics").json()
    assert body["userdict_words"] == 0

    settings.userdict_path.write_text("张仙姑 10000\n窗户 3\n", encoding="utf-8")
    assert client.get("/api/diagnostics").json()["userdict_words"] == 2


def test_month_budget_visible(client):
    body = client.get("/api/diagnostics").json()
    assert body["chars_this_month"] == 0
    assert body["month_limit"] == settings.translate_max_chars_per_month

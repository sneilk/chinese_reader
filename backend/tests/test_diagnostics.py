"""Ручка состояния сервиса.

Проверяется то, ради чего она существует: по ответу видно, что именно не
настроено. И отдельно — что в ответ не просочился ключ переводчика.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_session_factory, get_synthesizer
from app.config import settings
from app.db.base import Base
from app.db.models import Chapter, DictEntry, Document, Source, UserWord
from app.domain import ChapterStatus, ErrorKind
from app.main import app
from app.providers.speech import SpeechFailure, SpeechResult


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'diag.db'}")
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    with maker() as session:
        chapter = Chapter(
            document=Document(
                source=Source(kind="web", site="example.com"), key="https://example.com/"
            ),
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


# --- живая проверка озвучки ---
#
# Единственное на этом экране, чего нельзя узнать чтением настроек. «Ключ
# задан» горит зелёным и тогда, когда работать не будет: ключ может быть от
# того же сервисного аккаунта, что и у переводчика, а роль ai.speechkit-tts.user
# ему не выдана — и синтез ответит 403 при первом же нажатии «Слушать».


class FakeSynthesizer:
    voice = "alena"
    content_type = "audio/mpeg"
    signature = "fake"

    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.seen: list[str] = []

    async def synthesize(self, text: str) -> SpeechResult:
        self.seen.append(text)
        if self.failure is not None:
            raise self.failure
        return SpeechResult(audio=b"ID3" + b"x" * 2048, content_type=self.content_type,
                            chars_sent=len(text))


def test_speech_check_synthesizes_one_word(client):
    synth = FakeSynthesizer()
    app.dependency_overrides[get_synthesizer] = lambda: synth

    body = client.post("/api/diagnostics/speech-check").json()

    assert body["ok"] is True
    assert synth.seen == ["проверка"], "проверка должна стоить копейки, а не главу"
    assert "alena" in body["detail"]


def test_speech_check_reports_the_reason(client):
    """403 здесь почти всегда значит «роли нет» — и это должно быть видно."""
    app.dependency_overrides[get_synthesizer] = lambda: FakeSynthesizer(
        failure=SpeechFailure("HTTP 403 — проверьте роль ai.speechkit-tts.user")
    )

    body = client.post("/api/diagnostics/speech-check").json()

    assert body["ok"] is False
    assert body["kind"] == ErrorKind.SPEECH_FAILED
    assert "speechkit-tts" in body["detail"]


def test_speech_check_without_a_key(client):
    app.dependency_overrides[get_synthesizer] = lambda: None

    body = client.post("/api/diagnostics/speech-check").json()

    assert body["ok"] is False
    assert "ключ" in body["detail"]


def test_speech_check_does_not_run_by_itself(client):
    """Проверка тратит деньги, поэтому это POST: открытие экрана её не запускает."""
    synth = FakeSynthesizer()
    app.dependency_overrides[get_synthesizer] = lambda: synth

    client.get("/api/diagnostics")

    assert synth.seen == []

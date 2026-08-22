"""Клиент SpeechKit: форма запроса, формат ответа и поведение при отказах.

Парный к `test_translate.py` и устроен так же: сети нет, ответы провайдера
подставляются через `httpx.MockTransport`. Проверяется то, что определяет
работу вокруг него, — форма запроса, тип отдаваемого файла и различимость
причин отказа, — а не доступность чужого API.

Кэш и учёт расходов живут слоем выше и покрыты в `test_speech.py`. Здесь
только сам клиент.

Ретраи ускорены: настоящие паузы 5 и 20 секунд проверяли бы терпение.
"""

from urllib.parse import parse_qs

import httpx
import pytest

from app.config import settings
from app.providers.speech import API_URL, SpeechFailure, YandexSpeech

pytestmark = pytest.mark.anyio

MP3 = b"ID3\x04\x00fake-audio"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})


def _speech(handler, **kwargs) -> YandexSpeech:
    # Ключ и каталог латиницей не для красоты: HTTP-заголовки обязаны быть ASCII.
    kwargs.setdefault("api_key", "test-key")
    kwargs.setdefault("folder_id", "test-folder")
    kwargs.setdefault("retries", 0)
    return YandexSpeech(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), **kwargs)


def _form(request: httpx.Request) -> dict[str, str]:
    """Тело запроса v1 — form-urlencoded, а не JSON."""
    return {k: v[0] for k, v in parse_qs(request.read().decode()).items()}


# --- форма запроса ---


async def test_request_shape():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["content_type"] = request.headers.get("content-type", "")
        seen["auth"] = request.headers.get("authorization", "")
        seen["form"] = _form(request)
        return _ok(request)

    await _speech(handler, voice="alena", speed=1.0, audio_format="mp3").synthesize("Текст")

    assert seen["url"] == API_URL
    assert seen["auth"] == "Api-Key test-key"
    # JSON провайдер v1 не принимает — только форму.
    assert "application/x-www-form-urlencoded" in seen["content_type"]
    assert seen["form"]["folderId"] == "test-folder"
    assert seen["form"]["text"] == "Текст"
    assert seen["form"]["lang"] == "ru-RU"
    assert seen["form"]["voice"] == "alena"
    assert seen["form"]["format"] == "mp3"


async def test_emotion_sent_when_set():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["form"] = _form(request)
        return _ok(request)

    await _speech(handler, emotion="good").synthesize("Текст")
    assert seen["form"]["emotion"] == "good"


async def test_emotion_omitted_when_empty():
    """Пустая интонация означает «голос как есть», а не интонацию с именем ''."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["form"] = _form(request)
        return _ok(request)

    await _speech(handler, emotion="").synthesize("Текст")
    assert "emotion" not in seen["form"]


# --- ответ ---


async def test_returns_audio_and_billed_chars():
    got = await _speech(_ok).synthesize("Пять")
    assert got.audio == MP3
    assert got.chars_sent == 4


async def test_text_is_trimmed_before_counting():
    """Пробелы по краям озвучиваются молчанием, а тарифицируются как символы."""
    got = await _speech(_ok).synthesize("  Пять  ")
    assert got.chars_sent == 4


@pytest.mark.parametrize(
    ("audio_format", "content_type"),
    [("mp3", "audio/mpeg"), ("oggopus", "audio/ogg"), ("lpcm", "audio/x-pcm")],
)
async def test_content_type_follows_format(audio_format, content_type):
    """С этим типом файл уедет в браузер: ошибись — и `<audio>` его не примет."""
    speech = _speech(_ok, audio_format=audio_format)
    assert speech.content_type == content_type
    assert (await speech.synthesize("Текст")).content_type == content_type


def test_signature_covers_everything_that_changes_sound():
    """Ключ кэша строится из неё: смена голоса обязана дать новый файл."""
    base = _speech(_ok, voice="alena", emotion="", speed=1.0, audio_format="mp3").signature
    for changed in (
        _speech(_ok, voice="filipp", emotion="", speed=1.0, audio_format="mp3"),
        _speech(_ok, voice="alena", emotion="good", speed=1.0, audio_format="mp3"),
        _speech(_ok, voice="alena", emotion="", speed=1.5, audio_format="mp3"),
        _speech(_ok, voice="alena", emotion="", speed=1.0, audio_format="oggopus"),
    ):
        assert changed.signature != base


# --- отказы до сети ---


@pytest.mark.parametrize("text", ["", "   ", "\n"])
async def test_empty_text_fails_before_network(text):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("пустой текст в сеть уходить не должен")

    with pytest.raises(SpeechFailure, match="нечего"):
        await _speech(handler).synthesize(text)


async def test_missing_credentials_fail_before_network():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("без ключа в сеть ходить незачем")

    with pytest.raises(SpeechFailure, match="ключ"):
        await _speech(handler, api_key="").synthesize("Текст")


async def test_too_long_text_fails_before_network(monkeypatch):
    """Резать фразу пополам нельзя: два mp3 подряд склеиваются со щелчком."""
    monkeypatch.setattr(settings, "speech_max_chars_per_request", 10)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("длиннее лимита провайдера — отправлять нечего")

    with pytest.raises(SpeechFailure, match="длиннее лимита"):
        await _speech(handler).synthesize("а" * 11)


# --- отказы провайдера ---


async def test_forbidden_points_at_the_role():
    """403 здесь почти всегда значит «роли ai.speechkit-tts.user нет»."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(403, text="forbidden")

    with pytest.raises(SpeechFailure, match="speechkit-tts"):
        await _speech(handler, retries=2).synthesize("Текст")
    assert calls["n"] == 1, "недостающая роль ретраем не появится"


async def test_bad_request_is_not_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, text="bad voice")

    with pytest.raises(SpeechFailure, match="400"):
        await _speech(handler, retries=2).synthesize("Текст")
    assert calls["n"] == 1


async def test_retries_on_429_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="too many requests")
        return _ok(request)

    speech = _speech(handler, retries=1)
    monkeypatch.setattr(speech, "_backoff", lambda attempt: 0.0)

    assert (await speech.synthesize("Текст")).audio == MP3
    assert calls["n"] == 2


async def test_retries_on_timeout(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("слишком долго", request=request)
        return _ok(request)

    speech = _speech(handler, retries=1)
    monkeypatch.setattr(speech, "_backoff", lambda attempt: 0.0)

    assert (await speech.synthesize("Текст")).audio == MP3


async def test_gives_up_after_retries(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    speech = _speech(handler, retries=1)
    monkeypatch.setattr(speech, "_backoff", lambda attempt: 0.0)

    with pytest.raises(SpeechFailure, match="после 2 попыток"):
        await speech.synthesize("Текст")


async def test_network_error_is_not_retried():
    """Оборванное соединение — не перегрузка на той стороне."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("сеть недоступна", request=request)

    with pytest.raises(SpeechFailure, match="сеть"):
        await _speech(handler, retries=2).synthesize("Текст")


async def test_empty_body_is_rejected():
    """Пустой mp3 попал бы в кэш навсегда и играл бы тишину."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    with pytest.raises(SpeechFailure, match="пустой"):
        await _speech(handler).synthesize("Текст")

"""Переводчик: батчи, порядок, учёт символов и поведение при отказах.

Сети здесь нет: ответы провайдера подставляются через httpx.MockTransport.
Так проверяется ровно то, что нам важно, — раскладка ответа по предложениям и
реакция на 429, таймаут и битый ответ, — а не доступность чужого API.

Ретраи в тестах выключены или ускорены: настоящие паузы 5 и 20 секунд здесь
проверяли бы только терпение.
"""

import json

import httpx
import pytest

from app.providers.translate import (
    API_URL,
    TranslateFailure,
    YandexTranslate,
    billed_chars,
    make_batches,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _translator(handler, **kwargs) -> YandexTranslate:
    # Ключ и каталог латиницей не для красоты: HTTP-заголовки обязаны быть ASCII.
    kwargs.setdefault("api_key", "test-key")
    kwargs.setdefault("folder_id", "test-folder")
    kwargs.setdefault("retries", 0)
    return YandexTranslate(client=_client(handler), **kwargs)


def _echo(request: httpx.Request) -> httpx.Response:
    """Провайдер, возвращающий пометку на каждое предложение в том же порядке."""
    sent = json.loads(request.read().decode())["texts"]
    return httpx.Response(200, json={"translations": [{"text": f"пер:{t}"} for t in sent]})


# --- разбиение на батчи: чистая функция, сети не требует ---


def test_batches_fit_limit():
    texts = ["а" * 40, "б" * 40, "в" * 40]
    assert make_batches(texts, 100) == [[texts[0], texts[1]], [texts[2]]]


def test_batches_keep_order():
    texts = [str(i) * 10 for i in range(9)]
    assert [t for batch in make_batches(texts, 25) for t in batch] == texts


def test_single_batch_when_it_fits():
    """Глава в 3600 символов должна уходить одним запросом, а не двадцатью."""
    assert len(make_batches(["ф" * 30] * 120, 9000)) == 1


def test_oversized_sentence_goes_alone():
    """Резать предложение нельзя: перевод половины фразы бессмыслен."""
    texts = ["короткое", "д" * 500, "тоже короткое"]
    assert make_batches(texts, 100) == [["короткое"], ["д" * 500], ["тоже короткое"]]


def test_empty_input():
    assert make_batches([], 9000) == []


def test_billed_chars_counts_empty_as_one():
    """Пустой запрос тарифицируется как один символ — учёт должен совпадать со счётом."""
    assert billed_chars(["абв", ""]) == 4


# --- вызов провайдера ---


async def test_translations_come_back_in_order():
    got = await _translator(_echo).translate(["первое", "второе", "третье"])
    assert got.texts == ["пер:первое", "пер:второе", "пер:третье"]


async def test_request_shape():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.read().decode())
        return httpx.Response(200, json={"translations": [{"text": "перевод"}]})

    await _translator(handler).translate(["предложение"])

    assert seen["url"] == API_URL

    assert str(seen["headers"]["authorization"]) == "Api-Key test-key"
    assert seen["body"]["folderId"] == "test-folder"
    assert seen["body"]["sourceLanguageCode"] == "zh"  # обязателен для глоссария
    assert seen["body"]["targetLanguageCode"] == "ru"
    assert seen["body"]["format"] == "PLAIN_TEXT"
    assert seen["body"]["texts"] == ["предложение"]


async def test_counts_chars_and_requests():
    """`chars_sent` — то, за что выставят счёт; на нём стоит потолок расходов T1.11."""
    # 40+40 влезают в лимит, третий текст уходит вторым запросом.
    texts = ["а" * 40, "б" * 40, "в" * 40]
    got = await _translator(_echo, batch_chars=100).translate(texts)
    assert got.chars_sent == 120
    assert got.requests == 2


async def test_multiple_batches_keep_global_order():
    texts = [f"фраза{i}" for i in range(10)]
    got = await _translator(_echo, batch_chars=12).translate(texts)
    assert got.texts == [f"пер:{t}" for t in texts]
    assert got.requests > 1


async def test_empty_input_does_not_call_provider():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("пустой список не должен уходить в сеть")

    got = await _translator(handler).translate([])
    assert got == type(got)(texts=[], chars_sent=0, requests=0)


async def test_missing_credentials_fail_before_network():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("без ключа в сеть ходить незачем")

    with pytest.raises(TranslateFailure, match="ключ"):
        await _translator(handler, api_key="").translate(["текст"])


# --- отказы ---


async def test_retries_on_429_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="too many requests")
        return httpx.Response(200, json={"translations": [{"text": "перевод"}]})

    tr = _translator(handler, retries=1)
    monkeypatch.setattr(tr, "_backoff", lambda attempt: 0.0)

    got = await tr.translate(["текст"])
    assert got.texts == ["перевод"]
    assert calls["n"] == 2


async def test_gives_up_after_retries(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    tr = _translator(handler, retries=1)
    monkeypatch.setattr(tr, "_backoff", lambda attempt: 0.0)

    with pytest.raises(TranslateFailure, match="после 2 попыток"):
        await tr.translate(["текст"])


async def test_retries_on_timeout(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("слишком долго", request=request)
        return httpx.Response(200, json={"translations": [{"text": "перевод"}]})

    tr = _translator(handler, retries=1)
    monkeypatch.setattr(tr, "_backoff", lambda attempt: 0.0)

    assert (await tr.translate(["текст"])).texts == ["перевод"]


async def test_bad_key_is_not_retried():
    """401 ретраем не лечится — только лишние запросы и время пользователя."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text="unauthorized")

    with pytest.raises(TranslateFailure, match="401"):
        await _translator(handler, retries=2).translate(["текст"])
    assert calls["n"] == 1


async def test_short_answer_is_rejected():
    """Молчаливый сдвиг на один переведёт всю главу «не про то»."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"translations": [{"text": "только один"}]})

    with pytest.raises(TranslateFailure, match="вместо 2"):
        await _translator(handler).translate(["первое", "второе"])


async def test_broken_json_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="не json")

    with pytest.raises(TranslateFailure, match="JSON"):
        await _translator(handler).translate(["текст"])

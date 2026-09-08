"""Классификация отказов — единственная часть загрузчика, проверяемая без сети.

Она же определяет, что пользователь увидит вместо главы, поэтому проверяется
по случаям, а не «на глазок».
"""

import pytest

from app.domain import ErrorKind
from app.fetchers.base import classify, classify_navigation_error

CHALLENGE_TITLE = "Just a moment..."


@pytest.mark.parametrize(
    ("status", "title", "headers", "expected"),
    [
        # Норма
        (200, "无忧书城 - 为您免费提供在线阅读服务", {}, None),
        (200, "第1章", None, None),
        # Челлендж: и по заголовку, и по служебному заголовку ответа
        (403, CHALLENGE_TITLE, {}, ErrorKind.CHALLENGE),
        (403, "", {"cf-mitigated": "challenge"}, ErrorKind.CHALLENGE),
        (200, CHALLENGE_TITLE, {}, ErrorKind.CHALLENGE),
        (503, "Checking your browser before accessing", {}, ErrorKind.CHALLENGE),
        # Тот же челлендж на китайской локали: именно так его отдаёт
        # 51shucheng, и по одному только заголовку ответа он ловится не всегда.
        (403, "请稍候…", {}, ErrorKind.CHALLENGE),
        (200, "请稍候…", {}, ErrorKind.CHALLENGE),
        # Челлендж распознаётся раньше, чем 403 трактуется как отказ доступа
        (403, "Attention Required! | Cloudflare", {}, ErrorKind.CHALLENGE),
        # Обычные отказы
        (404, "Not Found", {}, ErrorKind.NOT_FOUND),
        (403, "Forbidden", {}, ErrorKind.NOT_FOUND),
        (408, "", {}, ErrorKind.FETCH_TIMEOUT),
        (504, "", {}, ErrorKind.FETCH_TIMEOUT),
        (500, "", {}, ErrorKind.ADAPTER_ERROR),
    ],
)
def test_classify(status, title, headers, expected):
    assert classify(status, title, headers) is expected


def test_headers_case_insensitive():
    """Регистр заголовков ответа не фиксирован спецификацией HTTP."""
    assert classify(403, "", {"CF-Mitigated": "challenge"}) is ErrorKind.CHALLENGE


def test_challenge_wins_over_404():
    """Порядок проверок: челлендж важнее кода, иначе он потеряется в 403/404."""
    assert classify(404, CHALLENGE_TITLE, {}) is ErrorKind.CHALLENGE


# --- отказы навигации: HTTP-ответа не случилось вовсе ---
#
# Тип исключения тут не помогает. `TimeoutError` Playwright бросает только на
# своём таймауте навигации; `net::ERR_TIMED_OUT` — это отказ сети, и приезжает
# он обычной ошибкой. Пока разбирали по типу, читатель на моргнувшем вайфае
# получал «покажите разработчику» вместо «попробуйте ещё раз».


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        # Сеть не дотянулась — лечится повтором.
        (
            "Page.goto: net::ERR_TIMED_OUT at https://novelarrow.com/chapter/x/45",
            ErrorKind.FETCH_TIMEOUT,
        ),
        ("Page.goto: net::ERR_CONNECTION_TIMED_OUT", ErrorKind.FETCH_TIMEOUT),
        ("Page.goto: net::ERR_CONNECTION_REFUSED", ErrorKind.FETCH_TIMEOUT),
        ("Page.goto: net::ERR_CONNECTION_RESET", ErrorKind.FETCH_TIMEOUT),
        ("Page.goto: net::ERR_EMPTY_RESPONSE", ErrorKind.FETCH_TIMEOUT),
        ("Page.goto: net::ERR_INTERNET_DISCONNECTED", ErrorKind.FETCH_TIMEOUT),
        ("Page.goto: net::ERR_NETWORK_CHANGED", ErrorKind.FETCH_TIMEOUT),
        # Адрес не разрешился — дело в самом адресе, повтор не поможет.
        (
            "Page.goto: net::ERR_NAME_NOT_RESOLVED at https://opechatka.example/",
            ErrorKind.NOT_FOUND,
        ),
        # Наша сторона: браузер упал, страницу закрыли.
        ("Target page, context or browser has been closed", ErrorKind.ADAPTER_ERROR),
        ("Protocol error (Page.navigate): Session closed.", ErrorKind.ADAPTER_ERROR),
        ("", ErrorKind.ADAPTER_ERROR),
    ],
)
def test_classify_navigation_error(message, expected):
    assert classify_navigation_error(message) is expected


def test_navigation_error_is_case_insensitive():
    """Chromium пишет код прописными, но полагаться на регистр незачем."""
    assert classify_navigation_error("net::err_timed_out") is ErrorKind.FETCH_TIMEOUT


def test_network_failure_is_not_reported_as_our_bug():
    """Тот самый случай с бою: адрес был живой, отказала сеть."""
    real = "Page.goto: net::ERR_TIMED_OUT at https://novelarrow.com/chapter/the-last-silver/45"

    assert classify_navigation_error(real) is not ErrorKind.ADAPTER_ERROR

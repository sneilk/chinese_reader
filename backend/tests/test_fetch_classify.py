"""Классификация отказов — единственная часть загрузчика, проверяемая без сети.

Она же определяет, что пользователь увидит вместо главы, поэтому проверяется
по случаям, а не «на глазок».
"""

import pytest

from app.domain import ErrorKind
from app.fetchers.base import classify

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

"""Ручная проверка сайта: окно браузера и снимок его экрана.

Само окно проверить без браузера нельзя, и не в нём здесь дело. Проверяется
договор ручки — то, из-за чего эта проверка вообще появилась.

Обычно челлендж проходится сам. Но если Cloudflare показал капчу, нажать на
неё может только человек, а до этой ручки такого случая просто не
существовало: глава уходила в `failed` с советом «попробуйте через минуту»,
который в этом случае не помогает никогда.

Отсюда три обязательства. Ручка должна честно сказать, что окна не будет
(загрузчик без браузера, headless-режим); должна отдать снимок экрана, потому
что без него на ВМ проверку не увидеть вовсе; и не должна принимать что попало
вместо адреса — открывать в браузере чужую строку хуже, чем её не открыть.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_fetcher
from app.config import settings
from app.domain import ErrorKind
from app.fetchers.browser import CheckResult
from app.main import app

URL = "https://www.51shucheng.net/renwen/kniga/1.html"


class FakeBrowser:
    """Загрузчик с окном. Помнит, о чём его просили."""

    def __init__(self, result: CheckResult | None = None) -> None:
        self.result = result
        self.calls: list[tuple[str, float]] = []

    async def get(self, url: str):  # pragma: no cover — здесь не нужен
        raise AssertionError("проверка не должна ходить обычной загрузкой")

    async def open_for_check(self, url, *, seconds=None, screenshot_path=None) -> CheckResult:
        self.calls.append((url, seconds))
        if screenshot_path is not None:
            Path(screenshot_path).parent.mkdir(parents=True, exist_ok=True)
            Path(screenshot_path).write_bytes(b"\x89PNG\r\n\x1a\nfake")
        return self.result or CheckResult(
            ok=True,
            kind=None,
            status=200,
            title="第1章 老三",
            url=url,
            waited_seconds=3.4,
            visible=True,
            screenshot=Path(screenshot_path) if screenshot_path else None,
        )


class FakeHeadlessFetcher:
    """Подставной загрузчик без браузера: окна у него нет."""

    async def get(self, url: str):  # pragma: no cover
        raise AssertionError("не должно вызываться")


@pytest.fixture
def browser():
    fetcher = FakeBrowser()
    app.dependency_overrides[get_fetcher] = lambda: fetcher
    yield fetcher
    app.dependency_overrides.clear()


@pytest.fixture
def client(browser):
    return TestClient(app)


# --- проверка ---


def test_check_reports_a_page_that_opened(client, browser):
    got = client.post("/api/diagnostics/browser-check", json={"url": URL}).json()

    assert got["ok"] is True
    assert got["kind"] is None
    assert got["status"] == 200
    assert got["title"] == "第1章 老三"
    assert got["waited_seconds"] == 3.4
    assert browser.calls == [(URL, 60.0)]


def test_check_reports_a_challenge(client, browser):
    browser.result = CheckResult(
        ok=False,
        kind=ErrorKind.CHALLENGE,
        status=403,
        title="请稍候…",
        url=URL,
        waited_seconds=60.0,
        visible=True,
        screenshot=None,
    )

    got = client.post("/api/diagnostics/browser-check", json={"url": URL}).json()

    assert got["ok"] is False
    assert got["kind"] == ErrorKind.CHALLENGE
    assert got["screenshot"] is False


def test_check_says_when_the_window_is_invisible(client, browser):
    """Headless — это «проходить нечего», а не «проверка не удалась»."""
    browser.result = CheckResult(
        ok=False,
        kind=ErrorKind.CHALLENGE,
        status=403,
        title="Just a moment...",
        url=URL,
        waited_seconds=5.0,
        visible=False,
        screenshot=None,
    )

    got = client.post("/api/diagnostics/browser-check", json={"url": URL}).json()

    assert got["visible"] is False


def test_wait_can_be_asked_for(client, browser):
    client.post("/api/diagnostics/browser-check", json={"url": URL, "seconds": 5})

    assert browser.calls == [(URL, 5.0)]


def test_wait_above_the_ceiling_is_rejected(client):
    r = client.post(
        "/api/diagnostics/browser-check",
        json={"url": URL, "seconds": settings.browser_check_timeout_seconds + 1},
    )

    assert r.status_code == 422
    assert r.json()["error"]["kind"] == "bad_request"


@pytest.mark.parametrize("bad", ["ftp://example.com/1.html", "просто текст", ""])
def test_bad_url_is_rejected(client, browser, bad):
    r = client.post("/api/diagnostics/browser-check", json={"url": bad})

    assert r.status_code == 422
    assert browser.calls == [], "в браузер такое не отправляем"


def test_fetcher_without_a_window_says_so():
    """Подставной загрузчик в тестах и любой будущий без браузера — один случай."""
    app.dependency_overrides[get_fetcher] = FakeHeadlessFetcher
    try:
        r = TestClient(app).post("/api/diagnostics/browser-check", json={"url": URL})
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 503
    assert r.json()["error"]["kind"] == ErrorKind.ADAPTER_ERROR


# --- снимок экрана ---


def test_screenshot_is_served_after_a_check(client, isolated_data_dir):
    client.post("/api/diagnostics/browser-check", json={"url": URL})

    r = client.get("/api/diagnostics/browser-check/screenshot")

    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG")


def test_screenshot_is_never_cached(client, isolated_data_dir):
    """Закэшированный показывал бы прошлую проверку вместо только что запущенной."""
    client.post("/api/diagnostics/browser-check", json={"url": URL})

    r = client.get("/api/diagnostics/browser-check/screenshot")

    assert r.headers["cache-control"] == "no-store"


def test_no_screenshot_yet_is_404(client, isolated_data_dir):
    r = client.get("/api/diagnostics/browser-check/screenshot")

    assert r.status_code == 404
    assert r.json()["error"]["kind"] == ErrorKind.NOT_FOUND

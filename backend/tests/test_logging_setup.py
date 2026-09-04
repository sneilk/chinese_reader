"""Логирование: уровень, формат и строка на запрос.

Проверять логи странно ровно до первой поломки, которую по ним разбирают.
Здесь три вещи, каждая из которых тихо ломает разбор, если ошибётся.

**Опечатка в уровне не должна ронять сервис.** `LOG_LEVEL` правят руками в
`/etc/chinese-reader/env`, и отказ старта из-за буквы отобрал бы инструмент
ровно тогда, когда он нужен.

**Опрос статуса не должен топить журнал.** Читалка спрашивает главу каждые
полторы секунды и получает 304. На INFO это десятки одинаковых строк в минуту,
между которыми теряется всё остальное.

**Middleware не должен трогать ответ.** Он стоит на пути каждого запроса, в
том числе за озвучкой: `FileResponse` отдаёт Range, без Range Safari на
iPhone не играет аудио вовсе, — и ошибка здесь выглядела бы как «озвучка
сломалась на телефоне», а не как «логи».
"""

import logging

import pytest
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient

from app.config import settings
from app.logging_setup import RequestLogMiddleware, journald_stamps_time, resolve_level

LOGGER = "app.logging_setup"


def ours(caplog) -> list[logging.LogRecord]:
    """Только наши записи: TestClient тащит с собой ещё и httpx с его строкой."""
    return [record for record in caplog.records if record.name == LOGGER]


# --- уровень из настройки ---


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("DEBUG", logging.DEBUG),
        ("info", logging.INFO),
        ("  Warning  ", logging.WARNING),
        ("ERROR", logging.ERROR),
    ],
)
def test_level_is_read_by_name(name, expected):
    assert resolve_level(name) == expected


@pytest.mark.parametrize("name", ["", "верно", "TRACE", "13"])
def test_unknown_level_falls_back_to_info(name):
    """Опечатка в настройке не отменяет логи — она отменяет только настройку."""
    assert resolve_level(name) == logging.INFO


# --- время в строке ---


def test_time_comes_from_journald_under_systemd(monkeypatch):
    """Под systemd время и имя юнита проставляет журнал: дублировать незачем."""
    monkeypatch.setenv("JOURNAL_STREAM", "8:12345")
    assert journald_stamps_time() is True


def test_time_is_ours_in_a_terminal(monkeypatch):
    """При `uvicorn --reload` штампа нет ниоткуда, и лог без времени бесполезен."""
    monkeypatch.delenv("JOURNAL_STREAM", raising=False)
    assert journald_stamps_time() is False


# --- строка на запрос ---


@pytest.fixture
def app_with_logging(tmp_path):
    app = FastAPI()

    @app.get("/ok")
    def ok() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/quiet")
    def quiet():
        from fastapi import Response

        return Response(status_code=304)

    @app.get("/bad")
    def bad():
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="not_found")

    @app.get("/file")
    def file() -> FileResponse:
        path = tmp_path / "audio.mp3"
        path.write_bytes(b"ID3" + b"x" * 100)
        return FileResponse(path, media_type="audio/mpeg")

    app.add_middleware(RequestLogMiddleware)
    return app


def test_request_is_logged_with_status_and_time(app_with_logging, caplog, monkeypatch):
    monkeypatch.setattr(settings, "log_requests", True)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        TestClient(app_with_logging).get("/ok")

    assert any("GET /ok → 200" in record.getMessage() for record in ours(caplog))


def test_query_string_is_kept(app_with_logging, caplog, monkeypatch):
    """По строке запроса и видно, за какой именно главой ходили."""
    monkeypatch.setattr(settings, "log_requests", True)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        TestClient(app_with_logging).get("/ok?word=%E7%AA%97")

    assert any("word=" in record.getMessage() for record in ours(caplog))


def test_polling_does_not_flood_the_log(app_with_logging, caplog, monkeypatch):
    """304 — это не событие, а тишина, сказанная вслух."""
    monkeypatch.setattr(settings, "log_requests", True)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        TestClient(app_with_logging).get("/quiet")

    assert not ours(caplog), "опрос статуса на INFO писаться не должен"


def test_polling_is_visible_on_debug(app_with_logging, caplog, monkeypatch):
    monkeypatch.setattr(settings, "log_requests", True)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        TestClient(app_with_logging).get("/quiet")

    assert any("→ 304" in record.getMessage() for record in ours(caplog))


def test_client_error_is_a_warning(app_with_logging, caplog, monkeypatch):
    """Разбор по уровню — ровно по тому, кто виноват: 4xx нам, 5xx нам вдвойне."""
    monkeypatch.setattr(settings, "log_requests", True)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        TestClient(app_with_logging).get("/bad")

    levels = {record.levelno for record in ours(caplog)}
    assert logging.WARNING in levels


def test_logging_can_be_turned_off(app_with_logging, caplog, monkeypatch):
    monkeypatch.setattr(settings, "log_requests", False)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        TestClient(app_with_logging).get("/ok")

    assert not ours(caplog)


def test_range_requests_still_work(app_with_logging, monkeypatch):
    """Ради этого middleware и написан ASGI-классом, а не декоратором."""
    monkeypatch.setattr(settings, "log_requests", True)

    r = TestClient(app_with_logging).get("/file", headers={"range": "bytes=0-2"})

    assert r.status_code == 206
    assert r.content == b"ID3"


def test_response_body_is_untouched(app_with_logging, monkeypatch):
    monkeypatch.setattr(settings, "log_requests", True)

    r = TestClient(app_with_logging).get("/ok")

    assert r.status_code == 200
    assert r.json() == {"status": "ok"}

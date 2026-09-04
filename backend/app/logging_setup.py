"""Логирование сервиса: один формат, один уровень, одна точка настройки.

До этого модуля настройка была одной строкой в `main.py`, и из неё следовали
три неудобства, каждое из которых стоит рабочего времени ровно тогда, когда
что-то сломалось.

**Уровень был зашит в код.** Поднять `DEBUG` на боевой машине значило
править файл и выкладывать заново; теперь это переменная в
`/etc/chinese-reader/env`, то есть перезапуск юнита.

**Запросы не логировались вовсе.** Отказ, который читатель видел на телефоне,
на сервере не оставлял следа: в журнале была работа конвейера, но не было
самого обращения. Понять, дошёл ли запрос, было нельзя.

**Uvicorn писал своим форматом.** Две формы строк в одном потоке читаются
хуже, чем одна, и `grep` по ним приходится писать дважды.

## Время в строке — не всегда

Под systemd логи уходят в journald, который сам проставляет время и имя юнита:
дублировать их значит читать каждую строку дважды. Но в терминале при
`uvicorn --reload` штампа нет ниоткуда, и лог без времени бесполезен.

Различить эти два случая можно точно, а не настройкой: systemd выставляет
`JOURNAL_STREAM` процессам, чей вывод подключён к журналу. Есть переменная —
время ставит journald, нет — ставим сами.

## Опрос статуса не должен топить журнал

Пока идёт перевод, читалка спрашивает главу каждые полторы секунды и получает
`304 Not Modified`. На INFO это десятки одинаковых строк в минуту, между
которыми теряется всё остальное. Поэтому 304 пишется на DEBUG: это не событие,
а тишина, сказанная вслух.
"""

from __future__ import annotations

import logging
import os
import time

from starlette.types import ASGIApp

from app.config import settings

log = logging.getLogger(__name__)

#: Библиотеки, чей DEBUG не про нас: httpx печатает каждый запрос к
#: переводчику вместе с заголовками, а Playwright — протокол общения с
#: браузером целиком. На INFO они молчат, на DEBUG забивают вывод.
#:
#: jieba здесь не за компанию: он **сам** ставит своему логгеру DEBUG при
#: импорте, поэтому четыре строки про сборку префиксного словаря приезжают в
#: журнал на каждом старте, что бы ни стояло в настройке.
_NOISY = ("httpx", "httpcore", "asyncio", "playwright", "urllib3", "jieba")

#: Свой доступ мы логируем сами и знаем про 304 больше, чем uvicorn.
_UVICORN_ACCESS = "uvicorn.access"

_WITH_TIME = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_WITHOUT_TIME = "%(levelname)-7s %(name)s: %(message)s"


def journald_stamps_time() -> bool:
    """Проставляет ли время кто-то за нас. Под systemd — да."""
    return "JOURNAL_STREAM" in os.environ


def resolve_level(name: str) -> int:
    """Уровень по имени. Опечатка в настройке не должна ронять сервис.

    Логирование — то, чем разбирают поломки; уронить старт из-за буквы в
    `LOG_LEVEL` значило бы отобрать инструмент ровно тогда, когда он нужен.
    """
    level = logging.getLevelNamesMapping().get(name.strip().upper())
    if level is None:
        logging.getLogger(__name__).warning(
            "неизвестный уровень логирования %r, беру INFO", name
        )
        return logging.INFO
    return level


def setup_logging(level: str | None = None) -> None:
    """Настроить корневой логгер. Зовётся один раз, до создания приложения."""
    resolved = resolve_level(level or settings.log_level)
    fmt = _WITHOUT_TIME if journald_stamps_time() else _WITH_TIME

    logging.basicConfig(level=resolved, format=fmt, datefmt="%Y-%m-%d %H:%M:%S", force=True)

    for name in _NOISY:
        logging.getLogger(name).setLevel(max(logging.INFO, resolved))

    # Не «выключить», а «замолчать»: у uvicorn остаётся собственный логгер
    # старта и остановки, а строки доступа пишет middleware ниже.
    logging.getLogger(_UVICORN_ACCESS).handlers.clear()
    logging.getLogger(_UVICORN_ACCESS).propagate = False

    log.info(
        "логирование: уровень %s, запросы %s, время %s",
        logging.getLevelName(resolved),
        "пишем" if settings.log_requests else "не пишем",
        "от journald" if journald_stamps_time() else "своё",
    )


def _level_for(status: int) -> int:
    """Каким уровнем писать ответ. Разбор ровно по тому, кто виноват."""
    if status >= 500:
        return logging.ERROR
    if status >= 400:
        return logging.WARNING
    if status == 304:
        # Опрос статуса: содержимое не изменилось, событий не произошло.
        return logging.DEBUG
    return logging.INFO


class RequestLogMiddleware:
    """Строка на запрос: метод, путь, код и сколько это заняло.

    Написано ASGI-классом, а не `@app.middleware("http")`: тот оборачивает
    ответ в свой `StreamingResponse`, и `FileResponse` с озвучкой теряет
    поддержку Range-запросов — без них Safari на iPhone не играет аудио вовсе.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not settings.log_requests:
            await self.app(scope, receive, send)
            return

        started = time.monotonic()
        status = 0

        async def watched(message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, watched)
        except Exception:
            # Исключение не отменяет строку в журнале: без неё видно только
            # трейсбек, а какой запрос его вызвал — уже нет.
            log.exception(
                "%s %s — упало за %s мс",
                scope.get("method", "?"),
                _path(scope),
                _elapsed_ms(started),
            )
            raise

        log.log(
            _level_for(status),
            "%s %s → %s за %s мс",
            scope.get("method", "?"),
            _path(scope),
            status,
            _elapsed_ms(started),
        )


def _path(scope) -> str:
    query = scope.get("query_string", b"").decode("latin-1")
    return f"{scope.get('path', '')}?{query}" if query else scope.get("path", "")


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


__all__ = ["RequestLogMiddleware", "resolve_level", "setup_logging"]

"""Точка входа FastAPI."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.chapters import router as chapters_router
from app.api.health import router as health_router
from app.api.lookup import router as lookup_router
from app.api.words import router as words_router
from app.config import settings
from app.db.session import SessionLocal
from app.fetchers.browser import BrowserFetcher
from app.lang.segment import Segmenter
from app.providers.translate import YandexTranslate

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Общие на процесс ресурсы: браузер, словарь сегментатора, переводчик.

    Браузер здесь только создаётся — стартует он при первой загрузке, чтобы
    приложение поднималось и на машине без Xvfb.
    """
    app.state.session_factory = SessionLocal
    app.state.segmenter = Segmenter(settings.userdict_path)
    app.state.fetcher = BrowserFetcher()

    if settings.yc_translate_api_key and settings.yc_folder_id:
        app.state.translator = YandexTranslate()
    else:
        # Без ключа главы останавливаются на segmented и читаются без
        # переводов — это рабочий режим, а не поломка.
        app.state.translator = None
        log.warning("переводчик не настроен: главы будут доходить до segmented")

    try:
        yield
    finally:
        await app.state.fetcher.close()
        if app.state.translator is not None:
            await app.state.translator.close()


app = FastAPI(title="chinese_reader", version="0.1.0", lifespan=lifespan)

# В разработке фронт живёт на отдельном порту Vite. В бою фронт отдаёт
# reverse proxy с того же origin, и CORS не нужен вовсе.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Ловим родительский класс Starlette, а не HTTPException из FastAPI: свои
# отказы мы бросаем вторым, но 404 на несуществующий путь и 405 на неверный
# метод порождает маршрутизатор первым. Регистрация только на потомка оставляла
# бы их в чужом формате `{"detail": ...}` — и фронт разбирал бы две разные
# формы ошибки, не подозревая об этом.
@app.exception_handler(StarletteHTTPException)
async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Ошибки в едином виде (RFC §8): фронт разбирает их по `kind`, а не по тексту."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"kind": str(exc.detail), "message": ""}},
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    return JSONResponse(
        status_code=422,
        content={"error": {"kind": "bad_request", "message": str(first.get("msg", ""))}},
    )


app.include_router(health_router, prefix="/api")
app.include_router(chapters_router, prefix="/api")
app.include_router(lookup_router, prefix="/api")
app.include_router(words_router, prefix="/api")

"""Точка входа FastAPI."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DatabaseError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.books import router as books_router
from app.api.chapters import router as chapters_router
from app.api.diagnostics import router as diagnostics_router
from app.api.health import router as health_router
from app.api.lookup import router as lookup_router
from app.api.words import router as words_router
from app.config import settings
from app.db.session import SessionLocal
from app.fetchers.browser import BrowserFetcher
from app.lang.segment import Segmenter
from app.logging_setup import RequestLogMiddleware, setup_logging
from app.providers.speech import YandexSpeech
from app.providers.translate import YandexTranslate
from app.services import walks
from app.services.pipeline import recover_interrupted

log = logging.getLogger(__name__)

# Логи уходят в stdout, а под systemd — прямо в journald. Настройка целиком
# живёт в app/logging_setup.py: уровень, формат и строка на запрос. Секретов в
# логах нет и быть не должно — ключ и куки не пишутся нигде.
setup_logging()


def _recover_interrupted() -> None:
    """Починить главы, застрявшие в работе после прошлого запуска.

    Падать здесь нельзя. База может быть не создана вовсе — на первом запуске
    до `alembic upgrade`, — и отказ старта из-за уборки был бы хуже самого
    мусора: сервис не поднялся бы вообще, а починить его через интерфейс,
    который не работает, невозможно.
    """
    try:
        with SessionLocal() as session:
            recover_interrupted(session)
    except DatabaseError as e:
        log.warning("уборка после перезапуска пропущена, база не готова: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Общие на процесс ресурсы: браузер, словарь сегментатора, переводчик, синтез.

    Браузер здесь только создаётся — стартует он при первой загрузке, чтобы
    приложение поднималось и на машине без Xvfb.

    Сегментатор один и он китайский: английскому словарь для резки не нужен
    вовсе (`lang/segment_en.py`), поэтому второго объекта здесь нет.
    """
    app.state.session_factory = SessionLocal
    app.state.segmenter = Segmenter(settings.userdict_path)
    app.state.fetcher = BrowserFetcher()
    _recover_interrupted()

    if settings.yc_translate_api_key and settings.yc_folder_id:
        app.state.translator = YandexTranslate()
    else:
        # Без ключа главы останавливаются на segmented и читаются без
        # переводов — это рабочий режим, а не поломка.
        app.state.translator = None
        log.warning("переводчик не настроен: главы будут доходить до segmented")

    if settings.speech_api_key and settings.yc_folder_id:
        app.state.synthesizer = YandexSpeech()
    else:
        # Ровно та же логика, что у переводчика: без ключа глава читается,
        # просто не звучит. Кнопка озвучки при этом гаснет, а не отказывает.
        app.state.synthesizer = None
        log.warning("озвучка не настроена: перевод будет читаться глазами")

    try:
        yield
    finally:
        # До закрытия браузера: обход, переживший остановку приложения, не
        # должен ходить на сайт тем, что сейчас закроется. Времени выкладки это
        # не сокращает — uvicorn ждёт фоновые задачи раньше и снимает их сам по
        # --timeout-graceful-shutdown из юнита (services/walks.py).
        walks.stop_all()
        await app.state.fetcher.close()
        if app.state.translator is not None:
            await app.state.translator.close()
        if app.state.synthesizer is not None:
            await app.state.synthesizer.close()


app = FastAPI(title="chinese_reader", version="0.1.0", lifespan=lifespan)

# В разработке фронт живёт на отдельном порту Vite. В бою фронт отдаёт
# reverse proxy с того же origin, и CORS не нужен вовсе.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Добавляется последним, а значит стоит снаружи всех: время в строке журнала
# должно включать и CORS, и разбор тела, а не только работу ручки.
app.add_middleware(RequestLogMiddleware)


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
app.include_router(diagnostics_router, prefix="/api")
app.include_router(chapters_router, prefix="/api")
app.include_router(books_router, prefix="/api")
app.include_router(lookup_router, prefix="/api")
app.include_router(words_router, prefix="/api")

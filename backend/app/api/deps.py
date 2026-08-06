"""Зависимости API: общие на приложение ресурсы.

Загрузчик и сегментатор — по одному на процесс, и это не оптимизация: браузер
стартует секунды и держит один персистентный профиль, а словарь jieba грузится
около полусекунды на экземпляр. Оба живут в `app.state`, чтобы тест мог
подменить их без Playwright и без словаря.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.fetchers.base import Fetcher
from app.lang.segment import Segmenter
from app.providers.translate import Translator

SessionFactory = Callable[[], Session]


def get_session_factory(request: Request) -> SessionFactory:
    """Фоновой задаче нужна своя сессия: сессия запроса закроется раньше неё."""
    return getattr(request.app.state, "session_factory", SessionLocal)


def get_session(
    factory: Annotated[SessionFactory, Depends(get_session_factory)],
) -> Iterator[Session]:
    # Фабрика приходит зависимостью, а не вызовом соседней функции: иначе её
    # подмена в тестах доходила бы до фоновой задачи, но не до сессии запроса.
    with factory() as session:
        yield session


def get_fetcher(request: Request) -> Fetcher:
    return request.app.state.fetcher


def get_segmenter(request: Request) -> Segmenter:
    return request.app.state.segmenter


def get_translator(request: Request) -> Translator | None:
    """`None` означает «переводчик не настроен» — глава останется на segmented."""
    return getattr(request.app.state, "translator", None)


# Через Annotated, а не Depends в значении по умолчанию: так ручки читаются
# как обычные функции с типами, и линтер не спорит с вызовом в дефолте.
SessionDep = Annotated[Session, Depends(get_session)]
FactoryDep = Annotated[SessionFactory, Depends(get_session_factory)]
FetcherDep = Annotated[Fetcher, Depends(get_fetcher)]
SegmenterDep = Annotated[Segmenter, Depends(get_segmenter)]
TranslatorDep = Annotated[Translator | None, Depends(get_translator)]

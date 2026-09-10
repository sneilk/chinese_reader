"""Зависимости API: общие на приложение ресурсы.

Загрузчик и сегментатор — по одному на процесс, и это не оптимизация: браузер
стартует секунды и держит один персистентный профиль, а словарь jieba грузится
около полусекунды на экземпляр. Оба живут в `app.state`, чтобы тест мог
подменить их без Playwright и без словаря.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Annotated, TypeVar

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.session import SessionLocal
from app.domain import ErrorKind
from app.fetchers.base import Fetcher
from app.lang.segment import Segmenter
from app.providers.speech import Synthesizer
from app.providers.translate import Translator

SessionFactory = Callable[[], Session]

#: Любая модель: помощник ниже одинаково годится и главе, и книге.
T = TypeVar("T", bound=Base)


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


def get_optional_segmenter(request: Request) -> Segmenter | None:
    """Сегментатор там, где он желателен, но не обязателен.

    Правку границ надо сохранить в любом случае: если сегментатор почему-то
    не поднялся, слово всё равно попадёт в базу, а в userdict — из неё при
    следующей пересборке. Терять правку читателя из-за этого нельзя.
    """
    return getattr(request.app.state, "segmenter", None)


def get_translator(request: Request) -> Translator | None:
    """`None` означает «переводчик не настроен» — глава останется на segmented."""
    return getattr(request.app.state, "translator", None)


def get_synthesizer(request: Request) -> Synthesizer | None:
    """`None` означает «озвучка не настроена» — глава читается, но не звучит."""
    return getattr(request.app.state, "synthesizer", None)


def get_or_404(session: Session, model: type[T], entity_id: int) -> T:
    """Достать запись или отказать 404 в общем формате.

    Форма отказа тут важнее экономии строк. `kind` у ответа обязан быть
    `not_found`, потому что по нему фронт подбирает объяснение; напиши здесь
    что-нибудь своё — и читатель увидит «непонятную ошибку» вместо «главы по
    этому адресу нет». Пока копий было две, разойтись они не успели, но
    третья ручка списывала бы уже с той, что попалась под руку.
    """
    entity = session.get(model, entity_id)
    if entity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, ErrorKind.NOT_FOUND)
    return entity


# Через Annotated, а не Depends в значении по умолчанию: так ручки читаются
# как обычные функции с типами, и линтер не спорит с вызовом в дефолте.
SessionDep = Annotated[Session, Depends(get_session)]
FactoryDep = Annotated[SessionFactory, Depends(get_session_factory)]
FetcherDep = Annotated[Fetcher, Depends(get_fetcher)]
SegmenterDep = Annotated[Segmenter, Depends(get_segmenter)]
OptionalSegmenterDep = Annotated[Segmenter | None, Depends(get_optional_segmenter)]
TranslatorDep = Annotated[Translator | None, Depends(get_translator)]
SynthesizerDep = Annotated[Synthesizer | None, Depends(get_synthesizer)]

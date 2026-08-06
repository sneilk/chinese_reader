"""Ручки главы (RFC §8).

Загрузка асинхронная: `POST` отвечает сразу и ставит работу в фон, клиент
опрашивает статус. Синхронный вариант отпадает — браузер плюс перевод легко
перешагнут таймаут прокси (RFC §4).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from app.api.deps import (
    FactoryDep,
    FetcherDep,
    SegmenterDep,
    SessionDep,
    SessionFactory,
    TranslatorDep,
)
from app.api.schemas import ChapterAccepted, ChapterCreate, ChapterOut
from app.db.models import Chapter
from app.domain import ChapterStatus, ErrorKind
from app.fetchers.base import Fetcher
from app.lang.segment import Segmenter
from app.providers.translate import Translator
from app.services.chapters import get_or_create_chapter
from app.services.pipeline import run_chapter_pipeline, translate_chapter

log = logging.getLogger(__name__)

router = APIRouter(tags=["chapters"])


async def _run_pipeline_job(
    factory: SessionFactory,
    chapter_id: int,
    *,
    fetcher: Fetcher,
    segmenter: Segmenter,
    translator: Translator | None,
) -> None:
    with factory() as session:
        chapter = session.get(Chapter, chapter_id)
        if chapter is None:
            log.warning("фоновая задача: глава %s исчезла", chapter_id)
            return
        await run_chapter_pipeline(
            session, chapter, fetcher=fetcher, segmenter=segmenter, translator=translator
        )


async def _translate_job(factory: SessionFactory, chapter_id: int, translator: Translator) -> None:
    with factory() as session:
        chapter = session.get(Chapter, chapter_id)
        if chapter is None:
            return
        await translate_chapter(session, chapter, translator)


@router.post("/chapters", status_code=status.HTTP_202_ACCEPTED, response_model=ChapterAccepted)
def create_chapter(
    payload: ChapterCreate,
    background: BackgroundTasks,
    session: SessionDep,
    factory: FactoryDep,
    fetcher: FetcherDep,
    segmenter: SegmenterDep,
    translator: TranslatorDep,
) -> ChapterAccepted:
    """Поставить главу в очередь. Идемпотентно по URL.

    Повторный запрос на уже загруженную главу в сеть не идёт — это то самое
    «за одной главой ходим один раз» из концепции §1.3. Исключение одно:
    глава в `failed` перезапускается, потому что для читателя повторный запрос
    после отказа и есть кнопка «попробовать снова».
    """
    chapter, created = get_or_create_chapter(session, payload.url)
    retry = not created and chapter.status == ChapterStatus.FAILED

    if created or retry:
        background.add_task(
            _run_pipeline_job,
            factory,
            chapter.id,
            fetcher=fetcher,
            segmenter=segmenter,
            translator=translator,
        )

    return ChapterAccepted(id=chapter.id, status=ChapterStatus(chapter.status), created=created)


@router.get("/chapters/{chapter_id}", response_model=ChapterOut)
def read_chapter(chapter_id: int, session: SessionDep) -> ChapterOut:
    """Глава целиком: статус, а начиная с `segmented` — текст, токены и переводы."""
    chapter = session.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, ErrorKind.NOT_FOUND)
    return ChapterOut.of(chapter)


@router.post(
    "/chapters/{chapter_id}/translate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ChapterAccepted,
)
def retranslate_chapter(
    chapter_id: int,
    background: BackgroundTasks,
    session: SessionDep,
    factory: FactoryDep,
    translator: TranslatorDep,
) -> ChapterAccepted:
    """Дозалить перевод после отказа. Переведённые предложения не переотправляются."""
    chapter = session.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, ErrorKind.NOT_FOUND)
    if translator is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, ErrorKind.TRANSLATE_FAILED)
    if chapter.content is None:
        # Текста нет — переводить нечего, и это не ошибка перевода.
        raise HTTPException(status.HTTP_409_CONFLICT, ErrorKind.EMPTY_EXTRACT)

    background.add_task(_translate_job, factory, chapter.id, translator)
    return ChapterAccepted(id=chapter.id, status=ChapterStatus(chapter.status), created=False)

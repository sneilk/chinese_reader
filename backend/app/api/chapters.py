"""Ручки главы (RFC §8).

Загрузка асинхронная: `POST` отвечает сразу и ставит работу в фон, клиент
опрашивает статус. Синхронный вариант отпадает — браузер плюс перевод легко
перешагнут таймаут прокси (RFC §4).

Озвучка, наоборот, синхронная и поштучная. Разница не в сложности, а в том,
чего ждёт читатель: главу он ставит в очередь и уходит, а «прочитай мне это
предложение» — обычный запрос за файлом, который либо уже лежит в кэше, либо
синтезируется за секунду. Фоновая задача здесь добавила бы опрос статуса ради
одного mp3.
"""

from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from app.api.deps import (
    FactoryDep,
    FetcherDep,
    SegmenterDep,
    SessionDep,
    SessionFactory,
    SynthesizerDep,
    TranslatorDep,
)
from app.api.schemas import ChapterAccepted, ChapterCreate, ChapterOut
from app.db.models import Chapter, Sentence
from app.domain import ChapterStatus, ErrorKind
from app.fetchers.base import Fetcher
from app.lang.segment import Segmenter
from app.providers.speech import SpeechFailure
from app.providers.translate import Translator
from app.services import budget
from app.services.chapters import get_or_create_chapter
from app.services.pipeline import run_chapter_pipeline, translate_chapter, walk_chapters
from app.services.speech import audio_for_sentence

log = logging.getLogger(__name__)

router = APIRouter(tags=["chapters"])


async def _run_pipeline_job(
    factory: SessionFactory,
    chapter_id: int,
    *,
    fetcher: Fetcher,
    segmenter: Segmenter,
    translator: Translator | None,
    follow: int = 0,
    fetch: bool = True,
) -> None:
    """Загрузить главу и, если просили, пройти книгу дальше.

    `fetch=False` — глава уже загружена, и обход начинается прямо с неё. Без
    этого «догрузить ещё десять» стоило бы лишнего похода на сайт за тем, что
    и так лежит в базе (концепция §1.3).
    """
    with factory() as session:
        chapter = session.get(Chapter, chapter_id)
        if chapter is None:
            log.warning("фоновая задача: глава %s исчезла", chapter_id)
            return

        if fetch:
            await run_chapter_pipeline(
                session, chapter, fetcher=fetcher, segmenter=segmenter, translator=translator
            )
            if chapter.status == ChapterStatus.FAILED:
                return

        if follow:
            await walk_chapters(
                session,
                chapter,
                fetcher=fetcher,
                segmenter=segmenter,
                translator=translator,
                limit=follow,
            )


async def _translate_job(factory: SessionFactory, chapter_id: int, translator: Translator) -> None:
    with factory() as session:
        chapter = session.get(Chapter, chapter_id)
        if chapter is None:
            return
        await translate_chapter(session, chapter, translator)


def _get_or_404(session: SessionDep, chapter_id: int) -> Chapter:
    chapter = session.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, ErrorKind.NOT_FOUND)
    return chapter


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

    `follow` продолжает работу за границу этой главы: конвейер пойдёт вперёд
    по ссылкам «следующая глава». Идемпотентность при этом не теряется —
    уже загруженные главы обход перешагивает, не трогая сайт, — но повторный
    запрос с `follow` **осмысленно** делает работу: он догружает следующие.
    Поэтому обход запускается и на существующей главе, а не только на новой.
    """
    chapter, created = get_or_create_chapter(session, payload.url)
    retry = not created and chapter.status == ChapterStatus.FAILED

    if created or retry or payload.follow:
        background.add_task(
            _run_pipeline_job,
            factory,
            chapter.id,
            fetcher=fetcher,
            segmenter=segmenter,
            translator=translator,
            follow=payload.follow,
            fetch=created or retry,
        )

    return ChapterAccepted(id=chapter.id, status=ChapterStatus(chapter.status), created=created)


@router.get("/chapters/{chapter_id}", response_model=ChapterOut)
def read_chapter(
    chapter_id: int, session: SessionDep, request: Request, response: Response
) -> ChapterOut | Response:
    """Глава целиком: статус, а начиная с `segmented` — текст, токены и переводы.

    Отдавать главу целиком было верным решением для одного запроса (RFC §8) и
    стало неверным для опроса: пока идёт перевод, клиент спрашивает статус
    каждые полторы секунды и каждый раз получает те же девяносто килобайт
    текста и токенов ради одной строки прогресса.

    Чинится это не урезанием ответа, а условным запросом. Клиент присылает
    `If-None-Match`, и если с прошлого раза ничего не изменилось, он получает
    304 без тела — а браузер сам достаёт содержимое из своего кэша и отдаёт
    коду как обычный ответ. Клиентского кода это не требует вовсе.

    Считать метку по готовому ответу было бы поздно: к этому моменту токены уже
    разобраны из JSON и сериализованы обратно. Поэтому она собирается из
    дешёвых признаков — что меняется в главе, то в неё и входит.
    """
    chapter = _get_or_404(session, chapter_id)
    next_id = _next_chapter_id(session, chapter)
    etag = _etag(session, chapter, next_id)

    # Ревалидация на каждый запрос: содержимое меняется по ходу конвейера, и
    # отдать его из кэша без спроса значило бы показать вчерашний статус.
    headers = {"etag": etag, "cache-control": "no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

    response.headers.update(headers)
    return ChapterOut.of(chapter, next_chapter_id=next_id)


def _next_chapter_id(session: SessionDep, chapter: Chapter) -> int | None:
    """Загружена ли уже следующая глава. `None` — ещё нет или ссылки не было."""
    if not chapter.next_chapter_url:
        return None
    return session.scalar(select(Chapter.id).where(Chapter.url == chapter.next_chapter_url))


def _etag(session: SessionDep, chapter: Chapter, next_id: int | None) -> str:
    """Метка версии ответа. Меняется ровно тогда, когда меняется его содержимое.

    Одного `updated_at` главы не хватает: переводы живут в `sentences`, и по
    ходу перевода строка главы не меняется вовсе. Поэтому в метку входит и
    состояние переводов — иначе читатель смотрел бы на «перевожу…» до конца
    главы, получая 304 на уже готовый текст.

    `next_id` входит по той же причине: он не свойство главы, а факт о
    соседней, и появляется он ровно тогда, когда её загрузили.
    """
    translated, last = session.execute(
        select(func.count(Sentence.id), func.max(Sentence.translated_at)).where(
            Sentence.chapter_id == chapter.id, Sentence.translation.is_not(None)
        )
    ).one()

    raw = "|".join(
        str(part)
        for part in (chapter.id, chapter.updated_at, chapter.status, translated, last, next_id)
    )
    return f'"{hashlib.sha256(raw.encode()).hexdigest()[:32]}"'


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
    chapter = _get_or_404(session, chapter_id)
    if translator is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, ErrorKind.TRANSLATE_FAILED)
    if chapter.content is None:
        # Текста нет — переводить нечего, и это не ошибка перевода.
        raise HTTPException(status.HTTP_409_CONFLICT, ErrorKind.EMPTY_EXTRACT)

    background.add_task(_translate_job, factory, chapter.id, translator)
    return ChapterAccepted(id=chapter.id, status=ChapterStatus(chapter.status), created=False)


@router.get("/chapters/{chapter_id}/audio/{sentence_idx}")
async def read_sentence_audio(
    chapter_id: int,
    sentence_idx: int,
    session: SessionDep,
    synthesizer: SynthesizerDep,
) -> FileResponse:
    """Озвучка русского перевода одного предложения.

    Отдаётся файлом с диска, а не байтами в теле: `FileResponse` умеет
    Range-запросы, а без них Safari на iPhone не проигрывает аудио вовсе.

    Ключ кэша — содержимое перевода (`services/speech.py`), поэтому ETag здесь
    честный: у другого текста будет другой файл, и браузер это увидит.
    """
    chapter = _get_or_404(session, chapter_id)
    if synthesizer is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, ErrorKind.SPEECH_FAILED)

    sentence = session.scalar(
        select(Sentence).where(
            Sentence.chapter_id == chapter.id, Sentence.idx == sentence_idx
        )
    )
    if sentence is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, ErrorKind.NOT_FOUND)

    try:
        path, content_type = await audio_for_sentence(session, chapter, sentence, synthesizer)
    except budget.BudgetExceeded as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, ErrorKind.BUDGET_EXCEEDED) from e
    except SpeechFailure as e:
        if sentence.translation is None:
            # Переводить нечего — это состояние главы, а не сбой синтеза.
            raise HTTPException(status.HTTP_409_CONFLICT, ErrorKind.TRANSLATE_FAILED) from e
        log.warning("глава %s, предложение %s: %s", chapter_id, sentence_idx, e.detail)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, ErrorKind.SPEECH_FAILED) from e

    return FileResponse(
        path,
        media_type=content_type,
        headers={
            "cache-control": "private, max-age=604800",
            "etag": f'"{path.stem}"',
        },
    )

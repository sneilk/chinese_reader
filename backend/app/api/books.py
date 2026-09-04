"""Ручки книги: список, оглавление, правка карточки и выгрузка целиком.

Появились вместе с обходом цепочкой. Пока глава открывалась по ссылке и была
одна, список был не нужен; с `follow` их заводится два десятка за запрос, и без
оглавления они оказываются записями, к которым нет дороги — адрес знает только
сайт, а `id` только база.

Оглавление отдаётся без текста глав намеренно: двадцать глав с содержимым
весят больше самой книги, а нужны они ради одного нажатия.

## Выгрузка книги — фоновая работа с состоянием, а не запрос

У «ещё N глав» с экрана главы есть понятный конец: N штук, полминуты, ответ по
факту. У книги целиком его нет — 550 глав по две секунды паузы складываются в
час, — поэтому запуск и наблюдение разведены на два обращения: `POST` ставит
работу и отвечает сразу, `GET` рассказывает, сколько уже прошло. Состояние
живёт в памяти процесса (`services/walks.py`), потому что и сама работа живёт
там же.

Перевод по ходу выгрузки выключен по умолчанию, и это решение про деньги, а не
про осторожность: книга-образец — полтора миллиона символов, половина
месячного потолка, потраченная одним нажатием. Кто хочет — просит явно.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response, status

from app.api.deps import (
    FactoryDep,
    FetcherDep,
    SegmenterDep,
    SessionDep,
    SessionFactory,
    TranslatorDep,
)
from app.api.schemas import BookOut, BookUpdate, BookWalkOut, BookWalkStart, ChapterBrief
from app.config import settings
from app.db.models import Document
from app.domain import ErrorKind
from app.fetchers.base import Fetcher
from app.lang.segment import Segmenter
from app.providers.translate import Translator
from app.services import walks
from app.services.books import book_row, delete_book, list_books, list_chapters, rename_book
from app.services.pipeline import walk_book

log = logging.getLogger(__name__)

router = APIRouter(tags=["books"])


def _get_or_404(session: SessionDep, book_id: int) -> Document:
    document = session.get(Document, book_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, ErrorKind.NOT_FOUND)
    return document


@router.get("/books", response_model=list[BookOut])
def read_books(session: SessionDep) -> list[BookOut]:
    """Книги со счётчиками глав, свежие сверху."""
    return [BookOut.of(book) for book in list_books(session)]


@router.get("/books/{book_id}/chapters", response_model=list[ChapterBrief])
def read_book_chapters(book_id: int, session: SessionDep) -> list[ChapterBrief]:
    """Оглавление книги в порядке чтения."""
    _get_or_404(session, book_id)
    return [ChapterBrief.of(chapter) for chapter in list_chapters(session, book_id)]


@router.patch("/books/{book_id}", response_model=BookOut)
def update_book(book_id: int, payload: BookUpdate, session: SessionDep) -> BookOut:
    """Назвать книгу. Пустой заголовок возвращает показ по адресу."""
    document = _get_or_404(session, book_id)
    rename_book(session, document, payload.title)

    book = book_row(session, book_id)
    assert book is not None, "книга только что была на месте"
    return BookOut.of(book)


@router.delete("/books/{book_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_book(book_id: int, session: SessionDep) -> Response:
    """Удалить книгу вместе с главами.

    Слова из личного словаря это не трогает: контексты ссылаются на главу
    обнуляемым ключом, а текст предложения лежит в карточке копией именно
    затем, чтобы пережить удаление главы (RFC §7).
    """
    document = _get_or_404(session, book_id)
    delete_book(session, document)
    walks.forget(book_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _walk_job(
    factory: SessionFactory,
    book_id: int,
    *,
    fetcher: Fetcher,
    segmenter: Segmenter,
    translator: Translator | None,
    limit: int,
) -> None:
    """Выгрузить книгу до конца. Наружу не бросает: ловить некому."""
    stopped_by: str | None = None
    try:
        with factory() as session:
            result = await walk_book(
                session,
                book_id,
                fetcher=fetcher,
                segmenter=segmenter,
                translator=translator,
                limit=limit,
            )
            stopped_by = result.stopped_by
    except Exception:  # noqa: BLE001 — фоновой задаче падать некуда
        # Причина уезжает наружу голым `error_kind`, без имени исключения:
        # по нему интерфейс подбирает объяснение, и строка «adapter_error:
        # KeyError» не совпадёт ни с одной — читатель увидит «непонятная
        # ошибка» вместо «не удалось разобрать страницу». Подробность есть в
        # журнале, и там от неё больше пользы.
        log.exception("выгрузка книги %s оборвалась", book_id)
        stopped_by = ErrorKind.ADAPTER_ERROR
    finally:
        # Обязательно в finally: иначе после падения книга навсегда осталась бы
        # «выгружается», и повторить выгрузку стало бы нельзя.
        walks.finish(book_id, stopped_by)


@router.post(
    "/books/{book_id}/walk",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BookWalkOut,
)
def start_book_walk(
    book_id: int,
    payload: BookWalkStart,
    background: BackgroundTasks,
    session: SessionDep,
    factory: FactoryDep,
    fetcher: FetcherDep,
    segmenter: SegmenterDep,
    translator: TranslatorDep,
) -> BookWalkOut:
    """Выгрузить книгу целиком: идти вперёд, пока идётся.

    Второй запуск по той же книге отвечает состоянием первого, а не заводит
    ещё один обход: загрузчик всё равно ходит на сайт по одному запросу за раз,
    а счётчик от двух обходов показывал бы вдвое больше сделанного.
    """
    _get_or_404(session, book_id)

    walk = walks.start(book_id, settings.max_chapters_per_book)
    if walk is None:
        running = walks.current(book_id)
        assert running is not None, "обход только что был занят"
        return BookWalkOut.of(running)

    background.add_task(
        _walk_job,
        factory,
        book_id,
        fetcher=fetcher,
        segmenter=segmenter,
        translator=translator if payload.translate else None,
        limit=walk.limit,
    )
    return BookWalkOut.of(walk)


@router.get("/books/{book_id}/walk", response_model=BookWalkOut)
def read_book_walk(book_id: int, session: SessionDep) -> BookWalkOut:
    """Сколько уже выгружено. Экран книги спрашивает это, пока обход идёт."""
    _get_or_404(session, book_id)
    walk = walks.current(book_id)
    return BookWalkOut.of(walk) if walk is not None else BookWalkOut.idle(book_id)


@router.delete("/books/{book_id}/walk", response_model=BookWalkOut)
def stop_book_walk(book_id: int, session: SessionDep) -> BookWalkOut:
    """Прекратить выгрузку. Загруженное остаётся, продолжить можно той же кнопкой.

    Работа не убивается на середине: обход смотрит просьбу между главами и
    уходит, дописав текущую. Поэтому ответ приходит сразу, а `running` гаснет
    через секунду-две — ровно столько докачивается страница.

    Без этой ручки часовую выгрузку было нельзя остановить вовсе: нажал —
    и жди, потому что фоновую задачу ждёт и сам uvicorn при остановке сервиса.
    """
    _get_or_404(session, book_id)
    walk = walks.request_stop(book_id)
    return BookWalkOut.of(walk) if walk is not None else BookWalkOut.idle(book_id)

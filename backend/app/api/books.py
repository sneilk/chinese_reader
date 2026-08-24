"""Ручки книги: список книг и оглавление одной из них.

Появились вместе с обходом цепочкой. Пока глава открывалась по ссылке и была
одна, список был не нужен; с `follow` их заводится два десятка за запрос, и без
оглавления они оказываются записями, к которым нет дороги — адрес знает только
сайт, а `id` только база.

Оглавление отдаётся без текста глав намеренно: двадцать глав с содержимым
весят больше самой книги, а нужны они ради одного нажатия.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import SessionDep
from app.api.schemas import BookOut, ChapterBrief
from app.db.models import Document
from app.domain import ErrorKind
from app.services.books import list_books, list_chapters

router = APIRouter(tags=["books"])


@router.get("/books", response_model=list[BookOut])
def read_books(session: SessionDep) -> list[BookOut]:
    """Книги со счётчиками глав, свежие сверху."""
    return [
        BookOut(
            id=book.id,
            key=book.key,
            lang=book.lang,
            site=book.site,
            chapters=book.chapters,
            readable=book.readable,
        )
        for book in list_books(session)
    ]


@router.get("/books/{book_id}/chapters", response_model=list[ChapterBrief])
def read_book_chapters(book_id: int, session: SessionDep) -> list[ChapterBrief]:
    """Оглавление книги в порядке чтения."""
    if session.get(Document, book_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, ErrorKind.NOT_FOUND)
    return [ChapterBrief.of(chapter) for chapter in list_chapters(session, book_id)]

"""Книги и их главы: то, что нужно списку.

Появилось вместе с обходом цепочкой. До него книга была понятием бухгалтерским
— строкой, к которой привязаны главы, — и спрашивать о ней было нечего: главу
открывали по ссылке, и она же была единственной. Обход заводит их два десятка
за один запрос, и без списка они превращаются в записи, к которым нет дороги:
адрес известен только сайту, а `id` — только базе.

Заголовка у книги нет и здесь не выдумывается. У `documents.title` не берётся
ни `<title>` страницы, ни заголовок первой главы: первый — это «Глава 12 —
Книга | Сайт», второй — название главы, и подставить второе под первое значит
назвать книгу именем её двенадцатой главы. Наружу уезжает `key` — адрес книги
на сайте; как показать его человеку, решает интерфейс.

Порядок глав — по `idx`, а главы без него уходят в конец. `idx` проставляется,
только когда положение выведено из цепочки (`services/chapters.py`), поэтому
«в конце» здесь означает честное «неизвестно где», а не «последняя».
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db.models import Chapter, Document, Source
from app.domain import ChapterStatus

#: Статусы, при которых главу уже можно открыть и читать.
READABLE = (ChapterStatus.SEGMENTED, ChapterStatus.TRANSLATING, ChapterStatus.READY)


@dataclass(frozen=True)
class BookRow:
    id: int
    key: str
    lang: str
    site: str | None
    chapters: int
    readable: int


def list_books(session: Session) -> list[BookRow]:
    """Книги со счётчиками глав. Пустых книг не бывает — их не из чего завести."""
    readable = func.sum(case((Chapter.status.in_(READABLE), 1), else_=0))
    rows = session.execute(
        select(
            Document.id,
            Document.key,
            Document.lang,
            Source.site,
            func.count(Chapter.id),
            readable,
        )
        .join(Source, Source.id == Document.source_id)
        .outerjoin(Chapter, Chapter.document_id == Document.id)
        .group_by(Document.id)
        # Книга, к которой недавно возвращались, нужнее той, что лежит с весны.
        .order_by(func.max(Chapter.created_at).desc())
    ).all()

    return [
        BookRow(
            id=row[0],
            key=row[1],
            lang=row[2],
            site=row[3],
            chapters=int(row[4] or 0),
            readable=int(row[5] or 0),
        )
        for row in rows
    ]


def list_chapters(session: Session, document_id: int) -> list[Chapter]:
    """Главы книги в порядке чтения. Главы без известного места — в конце."""
    return list(
        session.scalars(
            select(Chapter)
            .where(Chapter.document_id == document_id)
            .order_by(Chapter.idx.is_(None), Chapter.idx, Chapter.id)
        ).all()
    )

"""Книги и их главы: то, что нужно списку.

Появилось вместе с обходом цепочкой. До него книга была понятием бухгалтерским
— строкой, к которой привязаны главы, — и спрашивать о ней было нечего: главу
открывали по ссылке, и она же была единственной. Обход заводит их два десятка
за один запрос, и без списка они превращаются в записи, к которым нет дороги:
адрес известен только сайту, а `id` — только базе.

Заголовка у книги нет и **автоматически** здесь не выдумывается. У
`documents.title` не берётся ни `<title>` страницы, ни заголовок первой главы:
первый — это «Глава 12 — Книга | Сайт», второй — название главы, и подставить
второе под первое значит назвать книгу именем её двенадцатой главы. Наружу
уезжает `key` — адрес книги на сайте; как показать его человеку, решает
интерфейс.

Но написать заголовок руками читатель вправе, и это не противоречит сказанному
выше, а следует из него: раз вывести название неоткуда, единственный, кто его
знает, — тот, кто книгу читает. Поэтому `title` правится, а не вычисляется, и
пустое значение возвращает книгу к показу по адресу.

Порядок глав — по `idx`, а главы без него уходят в конец. `idx` проставляется,
только когда положение выведено из цепочки (`services/chapters.py`), поэтому
«в конце» здесь означает честное «неизвестно где», а не «последняя».

## Удаление книги удаляет главы, но не трогает словарь

Каскад до глав и предложений — то, чего от удаления и ждут. А контексты
сохранённых слов ссылаются на главу через `ON DELETE SET NULL`: текст
предложения в карточке лежит копией именно затем, чтобы пережить удаление
главы (RFC §7). Слово, выученное по этой книге, остаётся выученным.

Строка `sources` при этом остаётся жить. Она описывает сайт, а не книгу, и на
неё смотрят все книги того же сайта; убирать её вместе с последней книгой
значило бы удалять справочник заодно с записью.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session

from app.db.models import Chapter, Document, Source
from app.domain import ChapterStatus

log = logging.getLogger(__name__)

#: Статусы, при которых главу уже можно открыть и читать.
READABLE = (ChapterStatus.SEGMENTED, ChapterStatus.TRANSLATING, ChapterStatus.READY)

#: Длиннее этого заголовок не влезет в колонку и не нужен никому.
MAX_TITLE_CHARS = 200


@dataclass(frozen=True)
class BookRow:
    id: int
    key: str
    #: Заголовок, написанный читателем. `None` — показывать адрес.
    title: str | None
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
            Document.title,
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
            title=row[2],
            lang=row[3],
            site=row[4],
            chapters=int(row[5] or 0),
            readable=int(row[6] or 0),
        )
        for row in rows
    ]


def book_row(session: Session, book_id: int) -> BookRow | None:
    """Одна книга в том же виде, в каком она едет в списке."""
    return next((book for book in list_books(session) if book.id == book_id), None)


def rename_book(session: Session, document: Document, title: str | None) -> Document:
    """Назвать книгу. Пустой заголовок стирает название, а не пишет пустоту.

    Разница видна на экране: `NULL` означает «показывать адрес», а пустая
    строка — «показывать ничего», и книга в списке превратилась бы в пробел.
    """
    cleaned = " ".join((title or "").split())[:MAX_TITLE_CHARS]
    document.title = cleaned or None
    session.commit()
    log.info("книга %s переименована: %r", document.id, document.title)
    return document


def delete_book(session: Session, document: Document) -> int:
    """Удалить книгу вместе с главами. Возвращает, сколько глав удалено.

    Главы сносятся одним запросом, а не обходом объектов: у книги-образца их
    550, и у каждой полторы сотни предложений — восемьдесят тысяч строк,
    которые ORM иначе поднимет в память ради того, чтобы их забыть. Предложения
    и счётчики слов уносит каскад базы (`PRAGMA foreign_keys=ON` в
    `db/session.py`), контексты сохранённых слов — обнуляются.
    """
    book_id = document.id
    removed = session.execute(
        delete(Chapter).where(Chapter.document_id == book_id)
    ).rowcount
    session.delete(document)
    session.commit()
    log.info("книга %s удалена вместе с %s главами", book_id, removed)
    return int(removed or 0)


def chain_tail(session: Session, document_id: int) -> Chapter | None:
    """Глава, от которой книга продолжается вперёд. `None` — книга пуста.

    Это конец известной нам цепочки: самая дальняя по `idx` глава **с
    текстом**. Главы без `idx` в расчёт не идут — их место в книге неизвестно,
    и продолжать книгу с неизвестного места значит гадать, куда она поедет.

    Про «с текстом» отдельно, потому что это не придирка. Ссылка вперёд
    записывается вместе с текстом и только вместе с ним: у главы, которая не
    загрузилась, её нет и быть не может. А в хвосте цепочки такая глава
    оказывается регулярно — именно на ней обход и остановился в прошлый раз,
    получив челлендж или попав под перезапуск. Взяв её началом, выгрузка книги
    заканчивалась бы мгновенно и молча: идти вперёд не от чего.

    Шагнув назад, к последней загруженной главе, мы получаем и продолжение, и
    повторную попытку для упавшей: обход перезагружает главы в `failed`, через
    которые проходит.

    Если текста нет ни у одной главы — книга не загрузилась вовсе. Тогда
    возвращается последняя заведённая: сказать об этом должен тот, кто просил
    выгрузку, а не пустота вместо ответа.
    """
    placed = select(Chapter).where(
        Chapter.document_id == document_id, Chapter.idx.is_not(None)
    )

    loaded = session.scalars(
        placed.where(Chapter.content.is_not(None)).order_by(Chapter.idx.desc()).limit(1)
    ).first()
    if loaded is not None:
        return loaded

    furthest = session.scalars(placed.order_by(Chapter.idx.desc()).limit(1)).first()
    if furthest is not None:
        return furthest

    return session.scalars(
        select(Chapter)
        .where(Chapter.document_id == document_id)
        .order_by(Chapter.id.desc())
        .limit(1)
    ).first()


def list_chapters(session: Session, document_id: int) -> list[Chapter]:
    """Главы книги в порядке чтения. Главы без известного места — в конце."""
    return list(
        session.scalars(
            select(Chapter)
            .where(Chapter.document_id == document_id)
            .order_by(Chapter.idx.is_(None), Chapter.idx, Chapter.id)
        ).all()
    )

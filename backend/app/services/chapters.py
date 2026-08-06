"""Заведение главы по URL.

Идемпотентность `POST /api/chapters` держится на `chapters.url UNIQUE`
(RFC §4): за одной главой мы ходим на сайт один раз, а повторный запрос
возвращает уже имеющуюся запись.

Про книгу. В схеме у `documents` нет собственного ключа — ни slug, ни url
(RFC §7), поэтому главы одной книги группируются по общему префиксу адреса:
`/{жанр}/{книга}/{номер}.html` без последнего сегмента. Это работает без
миграции и ровно настолько, насколько нужно MVP, где книга заводится одной
главой. Когда появится оглавление целиком, ключ книги станет колонкой — тогда
и переедем, а пока лишняя колонка была бы задел ради задела.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Chapter, Document, Source
from app.domain import ChapterStatus

log = logging.getLogger(__name__)


def book_prefix(url: str) -> str:
    """Адрес книги: URL главы без последнего сегмента."""
    parsed = urlparse(url)
    path = parsed.path.rsplit("/", 1)[0]
    return f"{parsed.scheme}://{parsed.netloc}{path}/"


def _get_or_create_source(session: Session, url: str) -> Source:
    site = (urlparse(url).hostname or "").lower()
    source = session.scalars(
        select(Source).where(Source.kind == "web", Source.site == site)
    ).first()
    if source is None:
        source = Source(kind="web", site=site, lang="zh")
        session.add(source)
        session.flush()
    return source


def _get_or_create_document(session: Session, url: str) -> Document:
    prefix = book_prefix(url)
    # Книгу опознаём по любой её уже загруженной главе: своего ключа у
    # documents нет, а url главы уникален и никуда не денется.
    sibling = session.scalars(select(Chapter).where(Chapter.url.startswith(prefix))).first()
    if sibling is not None:
        return sibling.document

    document = Document(source=_get_or_create_source(session, url), lang="zh")
    session.add(document)
    session.flush()
    return document


def get_or_create_chapter(session: Session, url: str) -> tuple[Chapter, bool]:
    """Найти главу по URL или завести новую. Второе значение — «завели сейчас»."""
    existing = session.scalars(select(Chapter).where(Chapter.url == url)).first()
    if existing is not None:
        return existing, False

    chapter = Chapter(
        document=_get_or_create_document(session, url),
        url=url,
        status=ChapterStatus.FETCHING,
    )
    session.add(chapter)
    session.commit()
    log.info("заведена глава %s: %s", chapter.id, url)
    return chapter, True

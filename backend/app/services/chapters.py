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

Про язык. При заведении он известен только предположительно — по адаптеру
сайта, а у generic-адаптера и вовсе никак. Поэтому здесь ставится догадка, а
уточняет её конвейер после разбора страницы (`pipeline.apply_language`):
язык — свойство текста, и до текста его знать неоткуда.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.registry import pick_adapter
from app.db.models import Chapter, Document, Source
from app.domain import ChapterStatus, Language

log = logging.getLogger(__name__)


def book_prefix(url: str) -> str:
    """Адрес книги: URL главы без последнего сегмента."""
    parsed = urlparse(url)
    path = parsed.path.rsplit("/", 1)[0]
    return f"{parsed.scheme}://{parsed.netloc}{path}/"


def guess_language(url: str) -> Language:
    """Язык по адресу: его объявляет адаптер сайта. Догадка, а не факт."""
    return pick_adapter(url).lang or Language.ZH


def _get_or_create_source(session: Session, url: str, lang: Language) -> Source:
    site = (urlparse(url).hostname or "").lower()
    source = session.scalars(
        select(Source).where(Source.kind == "web", Source.site == site)
    ).first()
    if source is None:
        source = Source(kind="web", site=site, lang=lang)
        session.add(source)
        session.flush()
    return source


def _get_or_create_document(session: Session, url: str, lang: Language) -> Document:
    prefix = book_prefix(url)
    # Книгу опознаём по любой её уже загруженной главе: своего ключа у
    # documents нет, а url главы уникален и никуда не денется.
    sibling = session.scalars(select(Chapter).where(Chapter.url.startswith(prefix))).first()
    if sibling is not None:
        return sibling.document

    document = Document(source=_get_or_create_source(session, url, lang), lang=lang)
    session.add(document)
    session.flush()
    return document


def get_or_create_chapter(session: Session, url: str) -> tuple[Chapter, bool]:
    """Найти главу по URL или завести новую. Второе значение — «завели сейчас»."""
    existing = session.scalars(select(Chapter).where(Chapter.url == url)).first()
    if existing is not None:
        return existing, False

    lang = guess_language(url)
    chapter = Chapter(
        document=_get_or_create_document(session, url, lang),
        url=url,
        lang=lang,
        status=ChapterStatus.FETCHING,
    )
    session.add(chapter)
    session.commit()
    log.info("заведена глава %s (%s): %s", chapter.id, lang, url)
    return chapter, True

"""Заведение главы по URL.

Идемпотентность `POST /api/chapters` держится на `chapters.url UNIQUE`
(RFC §4): за одной главой мы ходим на сайт один раз, а повторный запрос
возвращает уже имеющуюся запись.

Про книгу. Её ключ — адрес на сайте, то есть URL главы без последнего
сегмента: `/{жанр}/{книга}/{номер}.html` → `/{жанр}/{книга}/`. Раньше он
вычислялся на лету и книга опознавалась перебором соседей с общим префиксом;
теперь он записан в `documents.key`, и опознание стало поиском по уникальному
ключу вместо сканирования по `LIKE`.

Про место главы в книге. `chapters.idx` — не номер главы на сайте, а её
**позиция в известной нам цепочке**: вывести настоящий номер неоткуда, слаг
главы у Next.js его не содержит. Отсчёт ведётся от первой загруженной главы
книги, а каждая следующая встаёт за той, что на неё ссылается. Этого хватает
на единственное, ради чего порядок нужен, — показать главы книги списком в том
порядке, в каком их читают.

Глава, загруженная в середину книги отдельной ссылкой, места не получает и
остаётся без номера: врать про её положение хуже, чем не знать его.

Про язык. При заведении он известен только предположительно — по адаптеру
сайта, а у generic-адаптера и вовсе никак. Поэтому здесь ставится догадка, а
уточняет её конвейер после разбора страницы (`pipeline.apply_language`):
язык — свойство текста, и до текста его знать неоткуда.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.base import canonical_url
from app.adapters.registry import pick_adapter
from app.db.models import Chapter, Document, Source
from app.domain import ChapterStatus, Language

log = logging.getLogger(__name__)


def canonical(url: str) -> str:
    """Адрес главы в том виде, в каком он считается её тождеством.

    Правило запроса объявляет адаптер сайта — он один знает, значит ли тот
    что-нибудь по эту сторону (`adapters/base.canonical_url`).
    """
    return canonical_url(url, keep_query=pick_adapter(url).keeps_query)


def book_prefix(url: str) -> str:
    """Адрес книги: канонический URL главы без последнего сегмента.

    Считается от канонического, а не от того, что ввели: иначе `www.` в
    ссылке заводил бы вторую книгу поверх первой, и главы одного романа
    оказывались бы в двух разных оглавлениях.
    """
    parsed = urlparse(canonical(url))
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
    key = book_prefix(url)
    document = session.scalars(select(Document).where(Document.key == key)).first()
    if document is not None:
        return document

    document = Document(source=_get_or_create_source(session, url, lang), key=key, lang=lang)
    session.add(document)
    session.flush()
    return document


def _place_in_book(session: Session, document: Document, url: str) -> int | None:
    """Позиция главы в цепочке. `None` — определить её неоткуда.

    Два случая, когда позиция известна. Первый: на эту главу уже кто-то
    ссылается как на следующую — значит она идёт сразу за ним. Второй: книга
    заводится этой главой, и она сама становится точкой отсчёта.

    Всё остальное — глава, вставленная в середину книги отдельной ссылкой, —
    остаётся без номера. Придумать его можно только из воздуха, а список глав
    с выдуманным порядком хуже списка без порядка: по нему нельзя заметить,
    что чего-то не хватает.
    """
    predecessor = session.scalars(
        select(Chapter).where(
            Chapter.document_id == document.id, Chapter.next_chapter_url == url
        )
    ).first()
    if predecessor is not None and predecessor.idx is not None:
        return predecessor.idx + 1

    loaded = session.scalar(
        select(func.count()).select_from(Chapter).where(Chapter.document_id == document.id)
    )
    return 0 if not loaded else None


def get_or_create_chapter(session: Session, raw_url: str) -> tuple[Chapter, bool]:
    """Найти главу по URL или завести новую. Второе значение — «завели сейчас».

    Между проверкой и вставкой есть щель, и она не теоретическая: выгрузка
    книги и «ещё N глав» с экрана чтения — две фоновые задачи, и обе идут по
    одной цепочке. Вторая, придя к той же главе, получала `IntegrityError` на
    `chapters.url UNIQUE`, обход обрывался, а причиной читателю показывали
    `adapter_error` — то есть «покажите разработчику» вместо «эта глава уже
    есть».

    Уникальный ключ для того и стоит: он не даёт дублю появиться, а нам —
    повод перечитать. Кто успел первым, того запись и берём.
    """
    url = canonical(raw_url)
    existing = session.scalars(select(Chapter).where(Chapter.url == url)).first()
    if existing is not None:
        return existing, False

    lang = guess_language(url)
    document = _get_or_create_document(session, url, lang)
    chapter = Chapter(
        document=document,
        url=url,
        lang=lang,
        idx=_place_in_book(session, document, url),
        status=ChapterStatus.FETCHING,
    )
    session.add(chapter)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raced = session.scalars(select(Chapter).where(Chapter.url == url)).first()
        if raced is None:
            # Ключ нарушен, но не по этому адресу — значит дело не в гонке, и
            # молчать нельзя: наверху разберутся лучше, чем мы здесь угадаем.
            raise
        log.info("глава %s уже заведена параллельно: %s", raced.id, url)
        return raced, False

    log.info("заведена глава %s (%s, №%s): %s", chapter.id, lang, chapter.idx, url)
    return chapter, True

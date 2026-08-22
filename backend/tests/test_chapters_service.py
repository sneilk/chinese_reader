"""Заведение главы: идемпотентность, группировка в книгу и догадка о языке.

Через ручки это проверяется по касательной, а решается здесь и молча. Три
вещи, каждая из которых ломается незаметно.

**Идемпотентность по URL** — то, на чём держится «за одной главой ходим один
раз» (концепция §1.3). Сломайся она, и повторное открытие главы шло бы на сайт
заново, а на 51shucheng это лишний шанс получить проверку Cloudflare.

**Группировка в книгу по префиксу адреса.** Своего ключа у `documents` нет
(RFC §7), поэтому книга опознаётся по любой уже загруженной главе с общим
префиксом. Разъедься это — и обход книги завёл бы по документу на главу.

**Язык при заведении — догадка по адаптеру, а не факт.** Факт появляется после
разбора страницы (`pipeline.apply_language`); здесь важно лишь то, что догадка
разумна и что у generic-адреса она не мешает.
"""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models import Chapter, Document, Source
from app.domain import ChapterStatus, Language
from app.services.chapters import book_prefix, get_or_create_chapter, guess_language

SHUCHENG = "https://www.51shucheng.net/renwen/kniga/12345.html"
SHUCHENG_SIBLING = "https://www.51shucheng.net/renwen/kniga/12346.html"
SHUCHENG_OTHER_BOOK = "https://www.51shucheng.net/renwen/drugaya/1.html"
NOVELARROW = "https://novelarrow.com/novel/the-long-cartography/chapter-12"
NOVELARROW_SIBLING = "https://novelarrow.com/novel/the-long-cartography/chapter-13"


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'chapters.db'}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, class_=Session, expire_on_commit=False)() as s:
        yield s


def count(session, model) -> int:
    return len(session.scalars(select(model)).all())


# --- адрес книги ---


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (SHUCHENG, "https://www.51shucheng.net/renwen/kniga/"),
        (NOVELARROW, "https://novelarrow.com/novel/the-long-cartography/"),
        ("https://example.com/one.html", "https://example.com/"),
        ("https://example.com/a/b/c/d", "https://example.com/a/b/c/"),
    ],
)
def test_book_prefix(url, expected):
    assert book_prefix(url) == expected


def test_book_prefix_keeps_the_host():
    """Одинаковые пути на разных сайтах — разные книги, а не одна."""
    assert book_prefix("https://a.com/x/1") != book_prefix("https://b.com/x/1")


# --- догадка о языке ---


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (SHUCHENG, Language.ZH),
        (NOVELARROW, Language.EN),
        # У generic языка нет — берётся китайский, с которого всё начиналось.
        # Догадка всё равно будет уточнена после разбора страницы.
        ("https://example.com/story/1", Language.ZH),
    ],
)
def test_guess_language(url, expected):
    assert guess_language(url) is expected


# --- заведение ---


def test_creates_chapter_with_guessed_language(session):
    chapter, created = get_or_create_chapter(session, NOVELARROW)

    assert created is True
    assert chapter.id is not None
    assert chapter.lang == Language.EN
    assert chapter.status == ChapterStatus.FETCHING
    assert chapter.url == NOVELARROW


def test_repeat_returns_the_same_chapter(session):
    """На этом держится «за одной главой ходим один раз»."""
    first, created_first = get_or_create_chapter(session, SHUCHENG)
    second, created_second = get_or_create_chapter(session, SHUCHENG)

    assert created_first is True
    assert created_second is False
    assert second.id == first.id
    assert count(session, Chapter) == 1


def test_repeat_does_not_reset_progress(session):
    """Иначе повторный запрос стирал бы уже загруженную главу."""
    chapter, _ = get_or_create_chapter(session, SHUCHENG)
    chapter.status = ChapterStatus.READY
    chapter.content = "текст главы"
    session.commit()

    again, created = get_or_create_chapter(session, SHUCHENG)

    assert created is False
    assert again.status == ChapterStatus.READY
    assert again.content == "текст главы"


# --- книга и источник ---


def test_sibling_chapters_share_one_document(session):
    get_or_create_chapter(session, SHUCHENG)
    get_or_create_chapter(session, SHUCHENG_SIBLING)

    assert count(session, Document) == 1
    assert count(session, Chapter) == 2


def test_different_books_get_different_documents(session):
    get_or_create_chapter(session, SHUCHENG)
    get_or_create_chapter(session, SHUCHENG_OTHER_BOOK)

    assert count(session, Document) == 2


def test_one_source_per_site(session):
    """Книг у сайта много, а сайт один: источник переиспользуется."""
    get_or_create_chapter(session, SHUCHENG)
    get_or_create_chapter(session, SHUCHENG_OTHER_BOOK)

    assert count(session, Source) == 1
    assert session.scalars(select(Source)).one().site == "www.51shucheng.net"


def test_different_sites_get_different_sources(session):
    get_or_create_chapter(session, SHUCHENG)
    get_or_create_chapter(session, NOVELARROW)

    assert count(session, Source) == 2
    assert count(session, Document) == 2


def test_document_and_source_inherit_the_guess(session):
    get_or_create_chapter(session, NOVELARROW)

    assert session.scalars(select(Document)).one().lang == Language.EN
    assert session.scalars(select(Source)).one().lang == Language.EN


def test_book_is_recognised_by_any_loaded_chapter(session):
    """Своего ключа у книги нет — опознаётся она по соседям, в любом порядке."""
    get_or_create_chapter(session, NOVELARROW_SIBLING)
    get_or_create_chapter(session, NOVELARROW)

    assert count(session, Document) == 1


def test_source_kind_is_web(session):
    get_or_create_chapter(session, SHUCHENG)
    assert session.scalars(select(Source)).one().kind == "web"

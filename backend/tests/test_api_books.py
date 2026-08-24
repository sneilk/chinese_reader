"""Список книг и оглавление.

Появились затем, чтобы загруженные обходом главы не оказывались записями, к
которым нет дороги. Отсюда и то, что проверяется: счётчики, по которым видно,
что в книге есть, и порядок, в котором её читают.

Про порядок отдельно. `idx` — не номер главы на сайте, а позиция в известной
нам цепочке, и у главы, вставленной в середину отдельной ссылкой, его нет.
Такие уходят в конец списка, и это честное «неизвестно где», а не «последняя»:
подставить им выдуманный номер значило бы сделать оглавление, по которому
нельзя заметить, что чего-то не хватает.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_session_factory
from app.db.base import Base
from app.db.models import Chapter, Document, Source
from app.domain import ChapterStatus, ErrorKind, Language
from app.main import app

BOOK = "https://novelarrow.com/novel/the-long-cartography/"


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'books.db'}")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):
        dbapi_connection.cursor().execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


@pytest.fixture
def client(factory):
    app.dependency_overrides[get_session_factory] = lambda: factory
    yield TestClient(app)
    app.dependency_overrides.clear()


def make_book(factory, key=BOOK, site="novelarrow.com", lang=Language.EN) -> int:
    with factory() as session:
        document = Document(
            source=Source(kind="web", site=site, lang=lang), key=key, lang=lang
        )
        session.add(document)
        session.commit()
        return document.id


def add_chapter(factory, book_id: int, *, url: str, idx=None, status=ChapterStatus.READY, **fields):
    with factory() as session:
        session.add(
            Chapter(document_id=book_id, url=url, idx=idx, status=status, lang="en", **fields)
        )
        session.commit()


# --- список книг ---


def test_no_books_yet(client):
    assert client.get("/api/books").json() == []


def test_book_carries_its_address_and_language(client, factory):
    book_id = make_book(factory)
    add_chapter(factory, book_id, url=f"{BOOK}chapter-1", idx=0)

    got = client.get("/api/books").json()

    assert len(got) == 1
    assert got[0]["id"] == book_id
    assert got[0]["key"] == BOOK
    assert got[0]["site"] == "novelarrow.com"
    assert got[0]["lang"] == "en"


def test_counts_chapters_and_readable(client, factory):
    """Читаема — начиная с segmented: текст и токены уже есть."""
    book_id = make_book(factory)
    add_chapter(factory, book_id, url=f"{BOOK}chapter-1", idx=0, status=ChapterStatus.READY)
    add_chapter(factory, book_id, url=f"{BOOK}chapter-2", idx=1, status=ChapterStatus.SEGMENTED)
    add_chapter(factory, book_id, url=f"{BOOK}chapter-3", idx=2, status=ChapterStatus.FETCHING)
    add_chapter(factory, book_id, url=f"{BOOK}chapter-4", idx=3, status=ChapterStatus.FAILED)

    got = client.get("/api/books").json()[0]

    assert got["chapters"] == 4
    assert got["readable"] == 2


def test_books_are_listed_separately(client, factory):
    first = make_book(factory, key="https://a.com/kniga/", site="a.com")
    second = make_book(factory, key="https://b.com/kniga/", site="b.com")
    add_chapter(factory, first, url="https://a.com/kniga/1")
    add_chapter(factory, second, url="https://b.com/kniga/1")

    got = client.get("/api/books").json()
    assert {book["id"] for book in got} == {first, second}


# --- оглавление ---


def test_chapters_ordered_by_position(client, factory):
    book_id = make_book(factory)
    # Заводим вразнобой: порядок должен задавать idx, а не порядок вставки.
    add_chapter(factory, book_id, url=f"{BOOK}chapter-3", idx=2)
    add_chapter(factory, book_id, url=f"{BOOK}chapter-1", idx=0)
    add_chapter(factory, book_id, url=f"{BOOK}chapter-2", idx=1)

    got = client.get(f"/api/books/{book_id}/chapters").json()

    assert [c["idx"] for c in got] == [0, 1, 2]


def test_chapter_without_position_goes_last(client, factory):
    """Глава, вставленная в середину отдельной ссылкой, места не знает."""
    book_id = make_book(factory)
    add_chapter(factory, book_id, url=f"{BOOK}chapter-1", idx=0)
    add_chapter(factory, book_id, url=f"{BOOK}chapter-99", idx=None)
    add_chapter(factory, book_id, url=f"{BOOK}chapter-2", idx=1)

    got = client.get(f"/api/books/{book_id}/chapters").json()

    assert [c["idx"] for c in got] == [0, 1, None]


def test_chapter_brief_has_what_a_list_needs(client, factory):
    book_id = make_book(factory)
    add_chapter(factory, book_id, url=f"{BOOK}chapter-1", idx=0, title="Chapter 1: The Salt Road")

    got = client.get(f"/api/books/{book_id}/chapters").json()[0]

    assert got["title"] == "Chapter 1: The Salt Road"
    assert got["status"] == ChapterStatus.READY
    assert got["lang"] == "en"
    assert got["error"] is None


def test_chapter_brief_carries_no_text(client, factory):
    """Двадцать глав с содержимым весят больше книги, а нужны ради одного тапа."""
    book_id = make_book(factory)
    add_chapter(factory, book_id, url=f"{BOOK}chapter-1", idx=0, content="а" * 5000)

    got = client.get(f"/api/books/{book_id}/chapters").json()[0]

    assert "content" not in got
    assert "tokens" not in got
    assert "sentences" not in got


def test_failed_chapter_shows_its_reason(client, factory):
    """По оглавлению должно быть видно, куда не стоит идти и почему."""
    book_id = make_book(factory)
    add_chapter(
        factory,
        book_id,
        url=f"{BOOK}chapter-1",
        idx=0,
        status=ChapterStatus.FAILED,
        error_kind=ErrorKind.CHALLENGE,
        error_detail="сайт просит проверку",
    )

    got = client.get(f"/api/books/{book_id}/chapters").json()[0]

    assert got["error"]["kind"] == ErrorKind.CHALLENGE
    assert got["error"]["message"] == "сайт просит проверку"


def test_unknown_book_is_404(client):
    r = client.get("/api/books/999/chapters")

    assert r.status_code == 404
    assert r.json()["error"]["kind"] == ErrorKind.NOT_FOUND


def test_book_without_chapters_has_empty_table_of_contents(client, factory):
    book_id = make_book(factory)
    assert client.get(f"/api/books/{book_id}/chapters").json() == []

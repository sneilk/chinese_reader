"""Проверяем не ORM, а те ограничения схемы, на которые опирается логика."""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Chapter, ChapterStatus, Document, Sentence, Source


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):
        dbapi_connection.cursor().execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _chapter(session, url="https://example.com/1.html") -> Chapter:
    src = Source(kind="web", site="example.com", lang="zh")
    doc = Document(source=src, title="книга", lang="zh")
    ch = Chapter(document=doc, url=url, title="глава", status=ChapterStatus.FETCHING)
    session.add(ch)
    session.commit()
    return ch


def test_url_unique(session):
    """Повторный POST с тем же URL не должен заводить вторую главу."""
    _chapter(session)
    with pytest.raises(IntegrityError):
        _chapter(session)


def test_status_check_constraint(session):
    ch = _chapter(session)
    ch.status = "неведомый статус"
    with pytest.raises(IntegrityError):
        session.commit()


def test_sentence_idx_unique_per_chapter(session):
    ch = _chapter(session)
    session.add(Sentence(chapter_id=ch.id, idx=0, start_offset=0, end_offset=5))
    session.commit()
    session.add(Sentence(chapter_id=ch.id, idx=0, start_offset=5, end_offset=9))
    with pytest.raises(IntegrityError):
        session.commit()


def test_sentence_offsets_ordered(session):
    ch = _chapter(session)
    session.add(Sentence(chapter_id=ch.id, idx=0, start_offset=10, end_offset=3))
    with pytest.raises(IntegrityError):
        session.commit()


def test_translation_nullable(session):
    """Глава живёт без переводов: отказ переводчика не теряет текст."""
    ch = _chapter(session)
    session.add(Sentence(chapter_id=ch.id, idx=0, start_offset=0, end_offset=5))
    session.commit()
    assert session.get(Sentence, 1).translation is None


def test_delete_chapter_cascades_sentences(session):
    ch = _chapter(session)
    session.add(Sentence(chapter_id=ch.id, idx=0, start_offset=0, end_offset=5))
    session.commit()
    session.delete(ch)
    session.commit()
    assert session.query(Sentence).count() == 0

"""Потолок расходов на перевод.

Потолок мягкий: он останавливает отправку, а не чтение. Поэтому проверяется
не только «отказал», но и «глава осталась читаемой» — и что причина отличима
от сбоя провайдера, потому что чинится она по-другому.

Лимиты берутся из настроек, а настройки читаются один раз при импорте, так
что в тестах их подменяет monkeypatch прямо на объекте.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.config import settings
from app.db.base import Base
from app.db.models import Chapter, Document, Source, TranslationUsage
from app.domain import ChapterStatus, ErrorKind
from app.services import budget
from app.services.pipeline import translate_chapter

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'budget.db'}")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):
        dbapi_connection.cursor().execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def limits(monkeypatch):
    """Удобные для счёта лимиты вместо боевых."""
    monkeypatch.setattr(settings, "translate_max_chars_per_chapter", 100)
    monkeypatch.setattr(settings, "translate_max_chars_per_month", 250)


def _usage(session, chars: int, *, when: datetime | None = None) -> None:
    row = TranslationUsage(provider="yandex", direction="zh-ru", chars_sent=chars, sentences=1)
    if when is not None:
        row.created_at = when
    session.add(row)
    session.commit()


# --- счёт за месяц ---


def test_month_start_is_calendar():
    """Месяц календарный, потому что так тарифицирует провайдер."""
    assert budget.month_start(datetime(2026, 8, 6, 15, 30)) == datetime(2026, 8, 1)


def test_chars_this_month_ignores_previous_months(session):
    now = datetime(2026, 8, 6, 12, 0)
    _usage(session, 100, when=now - timedelta(days=3))  # тот же месяц
    _usage(session, 500, when=datetime(2026, 7, 20))  # прошлый месяц

    assert budget.chars_this_month(session, now) == 100


def test_chars_this_month_on_empty_base(session):
    assert budget.chars_this_month(session) == 0


# --- проверка лимитов ---


def test_within_limits_passes(session, limits):
    budget.check(session, 50)


def test_chapter_limit_blocks(session, limits):
    with pytest.raises(budget.BudgetExceeded, match="на главу"):
        budget.check(session, 101)


def test_chapter_limit_counts_what_chapter_already_spent(session, limits):
    """Дозалив перевода не должен обходить лимит по частям."""
    with pytest.raises(budget.BudgetExceeded, match="на главу"):
        budget.check(session, 60, spent_on_chapter=60)


def test_month_limit_blocks(session, limits):
    _usage(session, 200)
    with pytest.raises(budget.BudgetExceeded, match="на месяц"):
        budget.check(session, 60)


def test_zero_limit_means_no_limit(session, monkeypatch):
    """Ноль в конфиге — «не ограничивать», иначе перевод молча не работал бы."""
    monkeypatch.setattr(settings, "translate_max_chars_per_chapter", 0)
    monkeypatch.setattr(settings, "translate_max_chars_per_month", 0)
    budget.check(session, 10_000_000)


def test_record_writes_usage(session):
    budget.record(session, provider="yandex", direction="zh-ru", chars_sent=42, sentences=3)
    row = session.query(TranslationUsage).one()
    assert (row.chars_sent, row.sentences, row.provider) == (42, 3, "yandex")


# --- поведение конвейера на превышении ---


class FakeTranslator:
    def __init__(self) -> None:
        self.calls = 0

    async def translate(self, texts, *, source: str = "zh"):
        self.calls += 1
        raise AssertionError("при превышении лимита в сеть ходить нельзя")


@pytest.fixture
def chapter(session) -> Chapter:
    src = Source(kind="web", site="example.com", lang="zh")
    doc = Document(source=src, key="https://example.com/", lang="zh")
    ch = Chapter(
        document=doc,
        url="https://example.com/1.html",
        status=ChapterStatus.SEGMENTED,
        content="他站起来走到窗户旁边，看见外面下着大雨，心里忽然安静了下来。",
    )
    session.add(ch)
    session.commit()
    from app.db.models import Sentence

    ch.sentences.append(Sentence(idx=0, start_offset=0, end_offset=len(ch.content)))
    session.commit()
    return ch


async def test_over_limit_keeps_chapter_readable(session, chapter, limits, monkeypatch):
    monkeypatch.setattr(settings, "translate_max_chars_per_chapter", 5)
    translator = FakeTranslator()

    await translate_chapter(session, chapter, translator)

    assert translator.calls == 0, "деньги тратить не начали"
    assert chapter.status == ChapterStatus.SEGMENTED
    assert chapter.content, "глава осталась читаемой"


async def test_over_limit_kind_differs_from_provider_failure(session, chapter, monkeypatch):
    """Это не сбой провайдера: повтор не поможет, поможет решение потратить больше."""
    monkeypatch.setattr(settings, "translate_max_chars_per_chapter", 5)

    await translate_chapter(session, chapter, FakeTranslator())

    assert chapter.error_kind == ErrorKind.BUDGET_EXCEEDED
    assert chapter.error_kind != ErrorKind.TRANSLATE_FAILED
    assert "лимите 5" in chapter.error_detail


async def test_month_limit_stops_translation(session, chapter, monkeypatch):
    monkeypatch.setattr(settings, "translate_max_chars_per_chapter", 0)
    monkeypatch.setattr(settings, "translate_max_chars_per_month", 40)
    _usage(session, 39)

    await translate_chapter(session, chapter, FakeTranslator())

    assert chapter.error_kind == ErrorKind.BUDGET_EXCEEDED
    assert "на месяц" in chapter.error_detail

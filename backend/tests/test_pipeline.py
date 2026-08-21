"""Конвейер главы и статусная модель.

Проверяется то, ради чего статусов пять: где именно глава перестаёт быть
читаемой. Отказ до `segmented` — это `failed`, показывать нечего; отказ
переводчика — это `segmented` с текстом и токенами на месте.

Сети здесь нет: загрузчик и переводчик подставные. HTML собран из своих
предложений — на живой фикстуре это был бы тест адаптера, а он свой уже имеет.
"""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Chapter, Document, Sentence, Source
from app.domain import ChapterStatus, ErrorKind
from app.fetchers.base import FetchFailure, FetchResult
from app.lang.segment import Segmenter
from app.providers.translate import TranslateFailure, TranslateResult
from app.services.pipeline import run_chapter_pipeline, translate_chapter

pytestmark = pytest.mark.anyio

URL = "https://51shucheng.net/renwen/kniga/1.html"

# Адаптеру нужно не меньше 100 иероглифов, иначе он справедливо решит, что
# перед ним не глава. Предложения свои, разной длины — включая короткие
# реплики, на которых работает склейка из T1.7.
_PARAGRAPHS = [
    "天很黑，风从窗户外面吹进来，屋子里没有一点声音。",
    "他站起来走到门口，又停下来想了很久才把门打开。",
    "“你来了。”",
    "她没有回答，只是把手里的东西放在桌子上，然后坐了下来。",
    "外面的雨越下越大，路上已经看不见一个人影了。",
    "他们两个人就这样坐着，谁也没有先开口说话。",
]
HTML = (
    "<html><head><title>Глава</title></head><body>"
    '<h1 class="chapter-title">Первая глава</h1>'
    '<div id="neirong">' + "".join(f"<p>{p}</p>" for p in _PARAGRAPHS) + "</div>"
    "</body></html>"
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def segmenter() -> Segmenter:
    return Segmenter()


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):
        dbapi_connection.cursor().execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def chapter(session) -> Chapter:
    src = Source(kind="web", site="51shucheng.net", lang="zh")
    doc = Document(source=src, title="книга", lang="zh")
    ch = Chapter(document=doc, url=URL, status=ChapterStatus.FETCHING)
    session.add(ch)
    session.commit()
    return ch


class FakeFetcher:
    """Отдаёт заготовленный HTML или падает заготовленной ошибкой."""

    def __init__(self, html: str = HTML, failure: FetchFailure | None = None) -> None:
        self._html = html
        self._failure = failure
        self.calls = 0

    async def get(self, url: str) -> FetchResult:
        self.calls += 1
        if self._failure is not None:
            raise self._failure
        return FetchResult(url=url, status=200, html=self._html, title="Глава")


class FakeTranslator:
    """Переводит пометкой, чтобы порядок и раскладка были видны глазами."""

    def __init__(self, failure: Exception | None = None) -> None:
        self._failure = failure
        self.seen: list[list[str]] = []
        self.sources: list[str] = []

    async def translate(self, texts, *, source: str = "zh") -> TranslateResult:
        self.seen.append(list(texts))
        self.sources.append(str(source))
        if self._failure is not None:
            raise self._failure
        return TranslateResult(
            texts=[f"пер:{t}" for t in texts],
            chars_sent=sum(len(t) for t in texts),
            requests=1,
        )


async def _run(session, chapter, segmenter, *, fetcher=None, translator=None) -> Chapter:
    return await run_chapter_pipeline(
        session,
        chapter,
        fetcher=fetcher or FakeFetcher(),
        segmenter=segmenter,
        translator=translator,
    )


# --- happy path ---


async def test_full_run_ends_ready(session, chapter, segmenter):
    tr = FakeTranslator()
    await _run(session, chapter, segmenter, translator=tr)

    assert chapter.status == ChapterStatus.READY
    assert chapter.error_kind is None
    assert chapter.content and chapter.tokens_json
    assert chapter.title == "Первая глава"
    assert chapter.fetched_at is not None
    assert all(s.translation.startswith("пер:") for s in chapter.sentences)


async def test_sentence_offsets_slice_canon(session, chapter, segmenter):
    await _run(session, chapter, segmenter, translator=FakeTranslator())

    assert chapter.sentences, "предложений не должно быть ноль"
    for s in chapter.sentences:
        # Офсеты режут канон, а не то, что пришло с сайта.
        assert chapter.content[s.start_offset : s.end_offset].strip()


async def test_translator_gets_sentences_not_whole_chapter(session, chapter, segmenter):
    tr = FakeTranslator()
    await _run(session, chapter, segmenter, translator=tr)

    sent = tr.seen[0]
    assert len(sent) == len(chapter.sentences)
    assert sent[0] == chapter.content[: chapter.sentences[0].end_offset]


async def test_chars_sent_recorded(session, chapter, segmenter):
    """Потолок расходов T1.11 будет считать по этому полю."""
    tr = FakeTranslator()
    await _run(session, chapter, segmenter, translator=tr)
    assert chapter.chars_sent == sum(len(t) for t in tr.seen[0])


async def test_without_translator_stops_at_segmented(session, chapter, segmenter):
    """Глава читаема и без перевода — это не отказ, а промежуточное состояние."""
    await _run(session, chapter, segmenter)

    assert chapter.status == ChapterStatus.SEGMENTED
    assert chapter.error_kind is None
    assert chapter.content


# --- отказы до segmented: показывать нечего ---


@pytest.mark.parametrize(
    "kind",
    [ErrorKind.CHALLENGE, ErrorKind.NOT_FOUND, ErrorKind.FETCH_TIMEOUT],
)
async def test_fetch_failure_marks_failed(session, chapter, segmenter, kind):
    fetcher = FakeFetcher(failure=FetchFailure(kind, "деталь"))
    await _run(session, chapter, segmenter, fetcher=fetcher, translator=FakeTranslator())

    assert chapter.status == ChapterStatus.FAILED
    assert chapter.error_kind == kind
    assert chapter.error_detail == "деталь"
    assert chapter.content is None


async def test_adapter_failure_marks_failed(session, chapter, segmenter):
    """Страница-оглавление отдаётся с 200, и распознать её может только адаптер."""
    fetcher = FakeFetcher(html="<html><body><p>тут нет главы</p></body></html>")
    await _run(session, chapter, segmenter, fetcher=fetcher, translator=FakeTranslator())

    assert chapter.status == ChapterStatus.FAILED
    assert chapter.error_kind == ErrorKind.EMPTY_EXTRACT


async def test_unexpected_error_does_not_leave_chapter_hanging(session, chapter, segmenter):
    """Фоновой задаче некуда падать: глава в fetching навсегда — вечный спиннер."""

    class Broken:
        async def get(self, url):
            raise ValueError("что-то совсем неожиданное")

    await _run(session, chapter, segmenter, fetcher=Broken(), translator=FakeTranslator())

    assert chapter.status == ChapterStatus.FAILED
    assert chapter.error_kind == ErrorKind.ADAPTER_ERROR
    assert "ValueError" in chapter.error_detail


# --- отказ перевода: глава остаётся читаемой ---


async def test_translate_failure_keeps_chapter_readable(session, chapter, segmenter):
    tr = FakeTranslator(failure=TranslateFailure("провайдер молчит"))
    await _run(session, chapter, segmenter, translator=tr)

    assert chapter.status == ChapterStatus.SEGMENTED
    assert chapter.error_kind == ErrorKind.TRANSLATE_FAILED
    assert chapter.error_detail == "провайдер молчит"
    assert chapter.content and chapter.tokens_json
    assert chapter.sentences, "предложения должны остаться"
    assert all(s.translation is None for s in chapter.sentences)


async def test_unexpected_translate_error_also_keeps_text(session, chapter, segmenter):
    tr = FakeTranslator(failure=RuntimeError("внезапно"))
    await _run(session, chapter, segmenter, translator=tr)

    assert chapter.status == ChapterStatus.SEGMENTED
    assert chapter.error_kind == ErrorKind.TRANSLATE_FAILED
    assert chapter.content


async def test_retry_after_failure_translates_and_clears_error(session, chapter, segmenter):
    """Повтор — отдельное действие, и он должен снимать прежнюю ошибку."""
    await _run(session, chapter, segmenter, translator=FakeTranslator(TranslateFailure("отказ")))
    assert chapter.status == ChapterStatus.SEGMENTED

    await translate_chapter(session, chapter, FakeTranslator())

    assert chapter.status == ChapterStatus.READY
    assert chapter.error_kind is None
    assert all(s.translation for s in chapter.sentences)


# --- повторный перевод ---


async def test_already_translated_sentences_are_not_resent(session, chapter, segmenter):
    """Повтор после частичного отказа стоит только недостающих символов."""
    await _run(session, chapter, segmenter, translator=FakeTranslator())
    spent = chapter.chars_sent

    second = FakeTranslator()
    await translate_chapter(session, chapter, second)

    assert second.seen == [], "в сеть ходить было незачем"
    assert chapter.chars_sent == spent
    assert chapter.status == ChapterStatus.READY


async def test_only_missing_sentences_are_sent(session, chapter, segmenter):
    await _run(session, chapter, segmenter, translator=FakeTranslator())

    # Одно предложение «потеряло» перевод — как после частичного отказа.
    victim = chapter.sentences[1]
    victim.translation = None
    session.commit()

    second = FakeTranslator()
    await translate_chapter(session, chapter, second)

    assert second.seen == [[chapter.content[victim.start_offset : victim.end_offset]]]
    assert all(s.translation for s in chapter.sentences)


async def test_rerun_does_not_duplicate_sentences(session, chapter, segmenter):
    """Переразбор той же главы не должен плодить предложения поверх старых."""
    await _run(session, chapter, segmenter, translator=FakeTranslator())
    first = len(chapter.sentences)

    await _run(session, chapter, segmenter, translator=FakeTranslator())

    assert len(chapter.sentences) == first
    assert session.query(Sentence).count() == first

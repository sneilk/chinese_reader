"""Английский режим целиком: от ссылки до озвучки.

Сквозной тест поверх подставных загрузчика, переводчика и синтезатора. Он
проверяет не отдельные функции — у каждой есть свой тест, — а то, что язык
доезжает по всей цепочке: адаптер объявил `en`, конвейер взял английские
правила резки, переводчик получил направление `en→ru`, а фронт получил `lang`
и не должен ничего угадывать.

Отдельный сюжет — обход книги. Оглавления у novelarrow из разметки не
достать, поэтому «загрузить десять глав» означает «пройти по ссылкам вперёд»,
и у такого обхода обязаны быть три свойства: он останавливается на конце
книги, останавливается на первом отказе и не ходит на сайт за тем, что уже
загружено.

Книга здесь своя, из трёх глав. Настоящий текст в тест класть незачем: адаптер
проверяется на своей фикстуре, а тут важна только его форма.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import (
    get_fetcher,
    get_segmenter,
    get_session_factory,
    get_synthesizer,
    get_translator,
)
from app.db.base import Base
from app.db.models import Chapter, Document
from app.domain import ChapterStatus, ErrorKind
from app.fetchers.base import FetchFailure, FetchResult
from app.lang.segment import Segmenter
from app.main import app
from app.providers.speech import SpeechFailure, SpeechResult
from app.providers.translate import TranslateResult

BOOK = "https://novelarrow.com/novel/the-long-cartography"
LAST = 3

# Абзацы главы: своих слов должно набраться больше сотни, иначе адаптер
# справедливо решит, что перед ним не глава. Предложения разной длины — с
# сокращением, инициалом и репликой, на которых работает английская резка.
_PARAGRAPHS = [
    "The road out of the lower town was white with salt, and it crunched underfoot "
    "like frost that had forgotten how to melt away.",
    "Ilsa counted the mileposts because counting kept her from listening to the wind, "
    "which had opinions about travellers and shared them freely.",
    '"You could still turn back," said the carter, who had said the same thing at '
    "every milepost since the third one.",
    "She did not answer him. Mr. Harrow had given the answer three days ago, in a room "
    "with a locked door and a witness who could not write it down.",
    "By the seventh milepost the salt had thinned and the road showed its old stones "
    "again, worn into shallow bowls by two hundred years of carts.",
    "The carter stopped talking. Somewhere ahead, past the low hills, a bell rang "
    "twice and then thought better of it entirely.",
]


def page(number: int) -> str:
    """Страница главы: заголовок, текст и кнопки перехода — как у Next.js-ридера."""
    body = "".join(f"<p>{p}</p>" for p in _PARAGRAPHS)
    nav = f'<a class="prev" href="{BOOK}/chapter-{number - 1}">Previous</a>' if number > 1 else ""
    if number < LAST:
        nav += f'<a class="next" href="{BOOK}/chapter-{number + 1}">Next Chapter</a>'
    return (
        "<html><head><title>Chapter | NovelArrow</title></head><body>"
        f"<h1>Chapter {number}: The Salt Road</h1>"
        f'<main><div class="rd"><div class="rd__body">{body}</div></div>'
        f'<div class="nav">{nav}</div></main>'
        "</body></html>"
    )


def url(number: int) -> str:
    return f"{BOOK}/chapter-{number}"


class FakeFetcher:
    """Отдаёт книгу из трёх глав. Считает походы на сайт по адресам."""

    def __init__(self) -> None:
        self.visited: list[str] = []
        self.failures: dict[str, FetchFailure] = {}

    async def get(self, target: str) -> FetchResult:
        self.visited.append(target)
        if target in self.failures:
            raise self.failures[target]
        number = int(target.rsplit("-", 1)[1])
        return FetchResult(url=target, status=200, html=page(number), title="Chapter")


class FakeTranslator:
    def __init__(self) -> None:
        self.sources: list[str] = []

    async def translate(self, texts, *, source: str = "zh") -> TranslateResult:
        self.sources.append(str(source))
        return TranslateResult(
            texts=[f"пер:{t}" for t in texts],
            chars_sent=sum(len(t) for t in texts),
            requests=1,
        )


class FakeSynthesizer:
    voice = "alena"
    content_type = "audio/mpeg"
    signature = "fake|alena|||mp3"

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.failure: Exception | None = None

    async def synthesize(self, text: str) -> SpeechResult:
        self.seen.append(text)
        if self.failure is not None:
            raise self.failure
        return SpeechResult(audio=b"ID3fake", content_type=self.content_type, chars_sent=len(text))


@pytest.fixture(scope="module")
def segmenter() -> Segmenter:
    return Segmenter()


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'en.db'}")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):
        dbapi_connection.cursor().execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


@pytest.fixture
def env(factory, segmenter):
    fetcher = FakeFetcher()
    translator = FakeTranslator()
    synthesizer = FakeSynthesizer()

    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_fetcher] = lambda: fetcher
    app.dependency_overrides[get_segmenter] = lambda: segmenter
    app.dependency_overrides[get_translator] = lambda: translator
    app.dependency_overrides[get_synthesizer] = lambda: synthesizer

    yield TestClient(app), fetcher, translator, synthesizer

    app.dependency_overrides.clear()


def load(client, number: int = 1, follow: int = 0) -> dict:
    accepted = client.post("/api/chapters", json={"url": url(number), "follow": follow}).json()
    return client.get(f"/api/chapters/{accepted['id']}").json()


# --- язык доезжает по всей цепочке ---


def test_chapter_is_english(env):
    client, _, _, _ = env
    got = load(client)

    assert got["status"] == ChapterStatus.READY
    assert got["lang"] == "en"
    assert got["title"] == "Chapter 1: The Salt Road"


def test_translator_gets_the_right_direction(env):
    """Направление задаёт глава, а не константа клиента."""
    client, _, translator, _ = env
    load(client)

    assert translator.sources == ["en"]


def test_tokens_are_latin_words(env):
    client, _, _, _ = env
    got = load(client)

    kinds = {kind for _, _, kind in got["tokens"]}
    assert "latin" in kinds
    assert "word" not in kinds, "иероглифических токенов в английской главе быть не может"


def test_tokens_cover_the_content(env):
    """Фронт собирает главу из токенов — дырка в покрытии это дырка на экране."""
    client, _, _, _ = env
    got = load(client)

    position = 0
    for start, end, _ in got["tokens"]:
        assert start == position
        position = end
    assert position == len(got["content"])


def test_sentences_split_by_english_rules(env):
    client, _, _, _ = env
    got = load(client)
    texts = [got["content"][s["start"] : s["end"]] for s in got["sentences"]]

    assert all(text == text.strip() for text in texts), "пробел после точки не часть фразы"
    assert all(s["translation"].startswith("пер:") for s in got["sentences"])
    # `Mr. Harrow` не должно оказаться концом предложения.
    assert not any(text.endswith("Mr.") for text in texts)


def test_document_language_follows_the_chapter(env, factory):
    """Язык книги подтягивается за главой: до разбора его знать было неоткуда."""
    client, _, _, _ = env
    load(client)

    with factory() as session:
        assert session.scalars(select(Document)).one().lang == "en"


# --- обход книги ---


def test_follow_loads_the_chain(env):
    client, fetcher, _, _ = env
    load(client, 1, follow=2)

    assert fetcher.visited == [url(1), url(2), url(3)]


def test_follow_stops_at_the_end_of_the_book(env):
    """У последней главы ссылки вперёд нет — просить больше нечего."""
    client, fetcher, _, _ = env
    load(client, 1, follow=10)

    assert fetcher.visited == [url(1), url(2), url(3)]


def test_follow_stops_on_the_first_failure(env):
    """Челлендж посреди книги — повод разобраться, а не идти дальше по инерции."""
    client, fetcher, _, _ = env
    fetcher.failures[url(2)] = FetchFailure(ErrorKind.CHALLENGE, "проверка")

    load(client, 1, follow=5)

    assert fetcher.visited == [url(1), url(2)]


def test_follow_skips_what_is_already_loaded(env):
    """Дочитал до места остановки, попросил ещё — должен получить новые главы."""
    client, fetcher, _, _ = env
    load(client, 1, follow=1)
    assert fetcher.visited == [url(1), url(2)]

    load(client, 1, follow=5)

    assert fetcher.visited == [url(1), url(2), url(3)], "за уже загруженным на сайт не ходим"


def test_follow_is_off_by_default(env):
    client, fetcher, _, _ = env
    load(client)

    assert fetcher.visited == [url(1)]


def test_follow_above_the_ceiling_is_rejected(env):
    client, fetcher, _, _ = env
    r = client.post("/api/chapters", json={"url": url(1), "follow": 500})

    assert r.status_code == 422
    assert r.json()["error"]["kind"] == "bad_request"
    assert fetcher.visited == []


# --- переход к следующей главе ---


def test_next_url_is_reported(env):
    client, _, _, _ = env
    got = load(client)

    assert got["next_url"] == url(2)
    assert got["next_chapter_id"] is None, "следующая ещё не загружена"


def test_next_chapter_id_appears_after_loading(env):
    client, _, _, _ = env
    first = load(client, 1, follow=1)

    got = load(client, 1)
    assert got["next_chapter_id"] is not None
    assert got["next_chapter_id"] != first["id"]


def test_last_chapter_has_no_next(env):
    client, _, _, _ = env
    load(client, 1, follow=2)
    got = load(client, LAST)

    assert got["next_url"] is None
    assert got["next_chapter_id"] is None


# --- озвучка ---


def test_audio_is_served(env):
    client, _, _, synthesizer = env
    chapter = load(client)

    r = client.get(f"/api/chapters/{chapter['id']}/audio/0")

    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.content == b"ID3fake"
    assert synthesizer.seen == [chapter["sentences"][0]["translation"]]


def test_audio_is_cached_between_requests(env):
    client, _, _, synthesizer = env
    chapter = load(client)

    client.get(f"/api/chapters/{chapter['id']}/audio/0")
    client.get(f"/api/chapters/{chapter['id']}/audio/0")

    assert len(synthesizer.seen) == 1


def test_audio_of_unknown_sentence_is_404(env):
    client, _, _, _ = env
    chapter = load(client)

    r = client.get(f"/api/chapters/{chapter['id']}/audio/9999")

    assert r.status_code == 404
    assert r.json()["error"]["kind"] == ErrorKind.NOT_FOUND


def test_audio_without_synthesizer_is_503(env):
    """Ключа нет — глава читается, просто не звучит. Это состояние, не поломка."""
    client, _, _, _ = env
    chapter = load(client)
    app.dependency_overrides[get_synthesizer] = lambda: None

    r = client.get(f"/api/chapters/{chapter['id']}/audio/0")

    assert r.status_code == 503
    assert r.json()["error"]["kind"] == ErrorKind.SPEECH_FAILED


def test_audio_without_translation_is_409(env, factory):
    """Озвучивать нечего: перевода нет — и это не сбой синтеза."""
    client, _, _, _ = env
    chapter = load(client)
    with factory() as session:
        stored = session.get(Chapter, chapter["id"])
        stored.sentences[0].translation = None
        session.commit()

    r = client.get(f"/api/chapters/{chapter['id']}/audio/0")

    assert r.status_code == 409
    assert r.json()["error"]["kind"] == ErrorKind.TRANSLATE_FAILED


def test_provider_failure_is_502(env):
    client, _, _, synthesizer = env
    chapter = load(client)
    synthesizer.failure = SpeechFailure("провайдер молчит")

    r = client.get(f"/api/chapters/{chapter['id']}/audio/0")

    assert r.status_code == 502
    assert r.json()["error"]["kind"] == ErrorKind.SPEECH_FAILED

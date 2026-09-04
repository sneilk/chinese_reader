"""Выгрузка книги целиком: как она начинается, чем кончается и что показывает.

От обхода «ещё N глав» отличается тремя вещами, и каждая здесь проверяется.

**Начало берётся из книги, а не от читателя.** С экрана книги неоткуда указать
главу, от которой идти дальше: там список, а не глава. Значит началом обязан
быть конец известной цепочки, и выбрать его должен сервис.

**За ней надо следить.** У книги-образца 550 глав по две секунды паузы — это
час работы с одного нажатия. Час без признаков жизни неотличим от зависшего
сервиса, поэтому у выгрузки есть состояние и его можно спросить.

**Перевод по умолчанию выключен.** Полтора миллиона символов — половина
месячного потолка, и потратить её молча, по умолчанию, нельзя.

Отдельный сюжет — глава без ссылки вперёд. Их в базе целая китайская половина:
они загружены тогда, когда адаптер 51shucheng ссылку не читал вовсе. Отличить
такую главу от последней главы книги по базе нельзя, поэтому у сайта
спрашивается заново — но так, чтобы не потерять уже оплаченные переводы.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_fetcher, get_segmenter, get_session_factory, get_translator
from app.db.base import Base
from app.db.models import Chapter, Sentence
from app.domain import ChapterStatus, ErrorKind
from app.fetchers.base import FetchFailure, FetchResult
from app.lang.segment import Segmenter
from app.main import app
from app.providers.translate import TranslateResult
from app.services import walks

BOOK = "https://novelarrow.com/novel/the-long-cartography"
LAST = 8

_PARAGRAPHS = [
    "The road out of the lower town was white with salt, and it crunched underfoot "
    "like frost that had forgotten how to melt away.",
    "Ilsa counted the mileposts because counting kept her from listening to the wind, "
    "which had opinions about travellers and shared them freely.",
    '"You could still turn back," said the carter, who had said the same thing at '
    "every milepost since the third one.",
    "She did not answer him. The answer had been given three days ago, in a room with "
    "a locked door and a witness who could not write it down.",
    "By the seventh milepost the salt had thinned and the road showed its old stones "
    "again, worn into shallow bowls by two hundred years of carts.",
    "The carter stopped talking. Somewhere ahead, past the low hills, a bell rang "
    "twice and then thought better of it entirely.",
]


def url(number: int) -> str:
    return f"{BOOK}/chapter-{number}"


def page(number: int) -> str:
    body = "".join(f"<p>{p}</p>" for p in _PARAGRAPHS)
    nav = f'<a class="prev" href="{url(number - 1)}">Previous</a>' if number > 1 else ""
    if number < LAST:
        nav += f'<a class="next" href="{url(number + 1)}">Next Chapter</a>'
    return (
        "<html><head><title>Chapter | NovelArrow</title></head><body>"
        f"<h1>Chapter {number}: The Salt Road</h1>"
        f'<main><div class="rd"><div class="rd__body">{body}</div></div>'
        f'<div class="nav">{nav}</div></main>'
        "</body></html>"
    )


class FakeFetcher:
    """Книга из восьми глав. Считает походы на сайт по адресам."""

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
        self.calls = 0

    async def translate(self, texts, *, source: str = "zh") -> TranslateResult:
        self.calls += 1
        return TranslateResult(
            texts=[f"пер:{t}" for t in texts],
            chars_sent=sum(len(t) for t in texts),
            requests=1,
        )


@pytest.fixture(scope="module")
def segmenter() -> Segmenter:
    return Segmenter()


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'walk.db'}")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):
        dbapi_connection.cursor().execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


@pytest.fixture(autouse=True)
def clean_walks():
    """Состояние обходов живёт в памяти процесса и между тестами не общее."""
    walks.reset()
    yield
    walks.reset()


@pytest.fixture
def env(factory, segmenter):
    fetcher = FakeFetcher()
    translator = FakeTranslator()

    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_fetcher] = lambda: fetcher
    app.dependency_overrides[get_segmenter] = lambda: segmenter
    app.dependency_overrides[get_translator] = lambda: translator

    yield TestClient(app), fetcher, translator

    app.dependency_overrides.clear()


def load_first(client) -> int:
    """Завести книгу первой главой. Возвращает её id."""
    client.post("/api/chapters", json={"url": url(1)})
    return client.get("/api/books").json()[0]["id"]


# --- выгрузка книги ---


def test_walk_loads_the_rest_of_the_book(env):
    client, _, _ = env
    book_id = load_first(client)

    r = client.post(f"/api/books/{book_id}/walk", json={})

    assert r.status_code == 202
    chapters = client.get(f"/api/books/{book_id}/chapters").json()
    assert len(chapters) == LAST
    assert [c["idx"] for c in chapters] == list(range(LAST))


def test_walk_reports_progress_when_it_is_over(env):
    client, _, _ = env
    book_id = load_first(client)

    client.post(f"/api/books/{book_id}/walk", json={})

    walk = client.get(f"/api/books/{book_id}/walk").json()
    assert walk["running"] is False
    assert walk["loaded"] == LAST - 1, "первая глава уже была, догрузились остальные"
    assert walk["stopped_by"] is None, "дошли до конца книги, а не оборвались"


def test_walk_answers_before_it_finishes(env):
    """Ответ приходит сразу: работа фоновая, и ждать её на запросе нечего."""
    client, _, _ = env
    book_id = load_first(client)

    got = client.post(f"/api/books/{book_id}/walk", json={}).json()

    assert got["running"] is True
    assert got["loaded"] == 0
    assert got["limit"] > 0


def test_walk_of_a_book_never_walked_is_idle(env):
    client, _, _ = env
    book_id = load_first(client)

    got = client.get(f"/api/books/{book_id}/walk").json()

    assert got == {
        "book_id": book_id,
        "running": False,
        "loaded": 0,
        "limit": 0,
        "stopped_by": None,
        "cancelled": False,
    }


def test_walk_does_not_translate_by_default(env):
    """Полтора миллиона символов молча тратить нельзя."""
    client, _, translator = env
    book_id = load_first(client)
    before = translator.calls

    client.post(f"/api/books/{book_id}/walk", json={})

    assert translator.calls == before, "перевода не просили"
    chapters = client.get(f"/api/books/{book_id}/chapters").json()
    assert all(c["status"] == ChapterStatus.SEGMENTED for c in chapters[1:])


def test_walk_translates_when_asked(env):
    client, _, translator = env
    book_id = load_first(client)
    before = translator.calls

    client.post(f"/api/books/{book_id}/walk", json={"translate": True})

    assert translator.calls == before + LAST - 1
    chapters = client.get(f"/api/books/{book_id}/chapters").json()
    assert all(c["status"] == ChapterStatus.READY for c in chapters)


def test_walk_skips_what_is_already_loaded(env):
    """Дочитав до места, где остановились, читатель должен получить новое."""
    client, fetcher, _ = env
    book_id = load_first(client)
    client.post("/api/chapters", json={"url": url(1), "follow": 2})
    visited = len(fetcher.visited)

    client.post(f"/api/books/{book_id}/walk", json={})

    fetched_again = [u for u in fetcher.visited[visited:] if u in (url(1), url(2), url(3))]
    assert fetched_again == [], "за уже загруженным на сайт не ходим"
    assert len(client.get(f"/api/books/{book_id}/chapters").json()) == LAST


def test_asking_for_n_more_gives_n_new_chapters(env):
    """«Ещё три» должно приносить три новых, а не три шага по уже известным.

    Дочитав до места, где остановились в прошлый раз, читатель нажимает «ещё
    три» — и если перешагивание через загруженное съедает потолок, получает
    ноль. Кнопка при этом выглядит нажатой и ничего не делает.
    """
    client, _, _ = env
    book_id = load_first(client)
    client.post("/api/chapters", json={"url": url(1), "follow": 3})
    assert len(client.get(f"/api/books/{book_id}/chapters").json()) == 4

    client.post("/api/chapters", json={"url": url(1), "follow": 3})

    assert len(client.get(f"/api/books/{book_id}/chapters").json()) == 7


def test_walk_stops_on_the_first_failure_and_says_why(env):
    client, fetcher, _ = env
    book_id = load_first(client)
    fetcher.failures[url(4)] = FetchFailure(ErrorKind.CHALLENGE, "сайт просит проверку")

    client.post(f"/api/books/{book_id}/walk", json={})

    walk = client.get(f"/api/books/{book_id}/walk").json()
    assert walk["running"] is False
    assert walk["stopped_by"] == ErrorKind.CHALLENGE
    assert walk["loaded"] == 2, "успели вторую и третью"


def test_walking_a_finished_book_again_finds_nothing_new(env):
    """Повтор на дочитанной книге разрешён, но работы не делает.

    Запрет второго **одновременного** обхода сюда не ловится и потому
    проверяется в test_walks.py: фоновая задача успевает закончиться раньше,
    чем приходит ответ, и «одновременного» здесь не бывает вовсе.
    """
    client, fetcher, _ = env
    book_id = load_first(client)
    client.post(f"/api/books/{book_id}/walk", json={})
    assert client.get(f"/api/books/{book_id}/walk").json()["loaded"] == LAST - 1
    visited = len(fetcher.visited)

    client.post(f"/api/books/{book_id}/walk", json={})

    assert client.get(f"/api/books/{book_id}/walk").json()["loaded"] == 0
    assert len(client.get(f"/api/books/{book_id}/chapters").json()) == LAST
    # Один поход всё же есть: у последней главы ссылки вперёд нет, и отличить
    # «книга кончилась» от «ссылку не читали» можно только спросив сайт.
    assert len(fetcher.visited) - visited <= 1


def test_walking_an_unknown_book_is_404(env):
    client, _, _ = env

    assert client.post("/api/books/999/walk", json={}).status_code == 404
    assert client.get("/api/books/999/walk").status_code == 404


def test_walk_of_an_empty_book_does_nothing(env, factory):
    """Книга без глав — не ошибка, просто идти неоткуда."""
    from app.db.models import Document, Source

    client, fetcher, _ = env
    with factory() as session:
        document = Document(source=Source(kind="web", site="x.com", lang="en"), key="k", lang="en")
        session.add(document)
        session.commit()
        book_id = document.id

    client.post(f"/api/books/{book_id}/walk", json={})

    assert fetcher.visited == []
    assert client.get(f"/api/books/{book_id}/walk").json()["running"] is False


def test_deleting_a_book_forgets_its_walk(env):
    """Иначе обход пережил бы книгу, к которой относился."""
    client, _, _ = env
    book_id = load_first(client)
    client.post(f"/api/books/{book_id}/walk", json={})

    client.delete(f"/api/books/{book_id}")

    assert walks.current(book_id) is None


# --- остановка ---
#
# Без неё выгрузка книги неостановима вовсе: фоновая задача живёт внутри
# запроса, и uvicorn при остановке сервиса её **ждёт** — «Waiting for
# background tasks to complete». Час ожидания на каждой выкладке.


def test_walk_stops_when_asked(env, factory):
    """Просьба смотрится между главами: текущая дописывается, дальше не идём."""
    client, fetcher, _ = env
    book_id = load_first(client)

    original = fetcher.get

    async def stop_after_three(target: str):
        result = await original(target)
        if len(fetcher.visited) >= 4:
            walks.request_stop(book_id)
        return result

    fetcher.get = stop_after_three
    client.post(f"/api/books/{book_id}/walk", json={})

    walk = client.get(f"/api/books/{book_id}/walk").json()
    assert walk["running"] is False
    assert walk["cancelled"] is True
    assert walk["stopped_by"] is None, "остановка по просьбе — не отказ"
    assert 0 < walk["loaded"] < LAST - 1, "ушли раньше конца книги"


def test_stopping_keeps_what_was_loaded(env):
    """Загруженное остаётся в книге: остановка не откатывает работу."""
    client, fetcher, _ = env
    book_id = load_first(client)

    original = fetcher.get

    async def stop_after_three(target: str):
        result = await original(target)
        if len(fetcher.visited) >= 4:
            walks.request_stop(book_id)
        return result

    fetcher.get = stop_after_three
    client.post(f"/api/books/{book_id}/walk", json={})

    loaded = client.get(f"/api/books/{book_id}/walk").json()["loaded"]
    chapters = client.get(f"/api/books/{book_id}/chapters").json()
    # Обе половины обязательны. Без первой утверждение выполняется и когда
    # обход не сделал ничего: ноль загруженных при одной главе в книге.
    assert 0 < loaded < LAST - 1
    assert len(chapters) == loaded + 1
    assert all(c["status"] == ChapterStatus.SEGMENTED for c in chapters[1:])


def test_a_stopped_walk_can_be_continued(env):
    """Иначе остановка означала бы «больше эту книгу не выгрузить»."""
    client, fetcher, _ = env
    book_id = load_first(client)

    original = fetcher.get

    async def stop_after_three(target: str):
        result = await original(target)
        if len(fetcher.visited) >= 4:
            walks.request_stop(book_id)
        return result

    fetcher.get = stop_after_three
    client.post(f"/api/books/{book_id}/walk", json={})

    fetcher.get = original
    client.post(f"/api/books/{book_id}/walk", json={})

    assert len(client.get(f"/api/books/{book_id}/chapters").json()) == LAST


def test_stop_request_answers_with_the_walk(env):
    client, _, _ = env
    book_id = load_first(client)
    client.post(f"/api/books/{book_id}/walk", json={})

    got = client.delete(f"/api/books/{book_id}/walk")

    assert got.status_code == 200
    assert got.json()["book_id"] == book_id


def test_stopping_a_book_never_walked_is_not_an_error(env):
    """Кнопка могла быть нажата на экране, открытом со вчера."""
    client, _, _ = env
    book_id = load_first(client)

    got = client.delete(f"/api/books/{book_id}/walk")

    assert got.status_code == 200
    assert got.json()["running"] is False


def test_stopping_an_unknown_book_is_404(env):
    client, _, _ = env
    assert client.delete("/api/books/999/walk").status_code == 404


def test_service_shutdown_stops_the_walk(env):
    """Выкладка не должна ждать час чужой работы."""
    client, fetcher, _ = env
    book_id = load_first(client)
    walks.stop_all()

    client.post(f"/api/books/{book_id}/walk", json={})

    assert fetcher.visited[1:] == [], "на сайт после просьбы не ходим"
    assert client.get(f"/api/books/{book_id}/walk").json()["loaded"] == 0


def test_shutdown_stops_the_plain_follow_too(env):
    """У «ещё N глав» записи об обходе нет, а ждать его при выкладке так же нечего."""
    client, fetcher, _ = env
    load_first(client)
    walks.stop_all()
    visited = len(fetcher.visited)

    client.post("/api/chapters", json={"url": url(1), "follow": 5})

    assert len(fetcher.visited) == visited


# --- глава без ссылки вперёд ---


def test_walk_asks_the_site_again_when_the_link_is_missing(env, factory):
    """Глава, загруженная до того, как ссылки научились читать, — не конец книги."""
    client, fetcher, _ = env
    book_id = load_first(client)
    with factory() as session:
        chapter = session.query(Chapter).one()
        chapter.next_chapter_url = None
        session.commit()

    client.post(f"/api/books/{book_id}/walk", json={})

    assert url(1) in fetcher.visited[1:], "за ссылкой сходили заново"
    assert len(client.get(f"/api/books/{book_id}/chapters").json()) == LAST


def test_a_failed_ask_is_not_the_end_of_the_book(env, factory):
    """Две ситуации выглядят одинаково — ссылки нет, — а означают разное.

    Сайт не дал ссылки — книга кончилась. До сайта не дошли — мы про конец
    книги вообще ничего не узнали. Сказав «готово, книга кончилась» на
    челлендже, выгрузка советует не делать ничего там, где надо пройти
    проверку.
    """
    client, fetcher, _ = env
    book_id = load_first(client)
    with factory() as session:
        session.query(Chapter).one().next_chapter_url = None
        session.commit()
    fetcher.failures[url(1)] = FetchFailure(ErrorKind.CHALLENGE, "челлендж")

    client.post(f"/api/books/{book_id}/walk", json={})

    assert client.get(f"/api/books/{book_id}/walk").json()["stopped_by"] == ErrorKind.CHALLENGE


def test_asking_again_does_not_lose_the_translations(env, factory):
    """Перезагрузка главы стёрла бы предложения вместе с оплаченным переводом."""
    client, _, _ = env
    book_id = load_first(client)
    first_id = client.get(f"/api/books/{book_id}/chapters").json()[0]["id"]
    client.post(f"/api/chapters/{first_id}/translate")
    with factory() as session:
        chapter = session.get(Chapter, first_id)
        chapter.next_chapter_url = None
        session.commit()
        before = [
            (s.idx, s.translation)
            for s in session.query(Sentence)
            .filter(Sentence.chapter_id == first_id)
            .order_by(Sentence.idx)
        ]

    client.post(f"/api/books/{book_id}/walk", json={})

    with factory() as session:
        after = [
            (s.idx, s.translation)
            for s in session.query(Sentence)
            .filter(Sentence.chapter_id == first_id)
            .order_by(Sentence.idx)
        ]
    assert before and after == before, "переводы первой главы обязаны остаться на месте"


def test_walk_continues_past_a_failed_tail(env, factory):
    """Обход прошлого раза упёрся в отказ, и упавшая глава осталась хвостом.

    Начав с неё, выгрузка не сделала бы ни шага: ссылки вперёд у главы без
    текста нет и быть не может. Шаг назад, к последней загруженной, даёт и
    продолжение книги, и повторную попытку для упавшей.
    """
    client, fetcher, _ = env
    book_id = load_first(client)
    fetcher.failures[url(2)] = FetchFailure(ErrorKind.CHALLENGE, "челлендж")
    client.post(f"/api/books/{book_id}/walk", json={})
    assert client.get(f"/api/books/{book_id}/walk").json()["stopped_by"] == ErrorKind.CHALLENGE

    fetcher.failures.clear()
    walks.reset()
    client.post(f"/api/books/{book_id}/walk", json={})

    chapters = client.get(f"/api/books/{book_id}/chapters").json()
    assert len(chapters) == LAST
    assert all(c["status"] != ChapterStatus.FAILED for c in chapters)


def test_a_book_that_never_loaded_says_why(env):
    """«Кнопка ничего не делает» — худший ответ из возможных."""
    client, fetcher, _ = env
    fetcher.failures[url(1)] = FetchFailure(ErrorKind.CHALLENGE, "челлендж")
    client.post("/api/chapters", json={"url": url(1)})
    book_id = client.get("/api/books").json()[0]["id"]

    client.post(f"/api/books/{book_id}/walk", json={})

    assert client.get(f"/api/books/{book_id}/walk").json()["stopped_by"] == ErrorKind.CHALLENGE


def test_the_end_of_the_book_is_not_an_error(env):
    """У последней главы ссылки вперёд нет по-настоящему — и это не отказ."""
    client, _, _ = env
    book_id = load_first(client)
    client.post(f"/api/books/{book_id}/walk", json={})
    walks.reset()

    client.post(f"/api/books/{book_id}/walk", json={})

    walk = client.get(f"/api/books/{book_id}/walk").json()
    assert walk["stopped_by"] is None
    assert walk["loaded"] == 0

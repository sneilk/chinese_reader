"""Ручки главы: идемпотентность, отдача целиком и все причины отказа.

TestClient создаётся без `with`, поэтому lifespan не запускается: браузер и
словарь приложению здесь не нужны, всё внешнее подменено. Фоновые задачи
starlette выполняет до возврата из запроса, так что после POST глава уже
прошла конвейер — опрашивать статус в цикле не приходится.

Сегментатор настоящий: он дешёвый, а тест от этого становится сквозным —
проверяется в том числе, что фронту доедут реальные токены.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_fetcher, get_segmenter, get_session_factory, get_translator
from app.db.base import Base
from app.db.models import Chapter
from app.domain import ChapterStatus, ErrorKind
from app.fetchers.base import FetchFailure, FetchResult
from app.lang.segment import Segmenter
from app.main import app
from app.providers.translate import TranslateFailure, TranslateResult

URL = "https://51shucheng.net/renwen/kniga/1.html"
OTHER_URL = "https://51shucheng.net/renwen/kniga/2.html"

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


class FakeFetcher:
    def __init__(self, html: str = HTML, failure: FetchFailure | None = None) -> None:
        self.html = html
        self.failure = failure
        self.calls = 0

    async def get(self, url: str) -> FetchResult:
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return FetchResult(url=url, status=200, html=self.html, title="Глава")


class FakeTranslator:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.calls = 0
        self.sources: list[str] = []

    async def translate(self, texts, *, source: str = "zh") -> TranslateResult:
        self.calls += 1
        self.sources.append(str(source))
        if self.failure is not None:
            raise self.failure
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
    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):
        dbapi_connection.cursor().execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


@pytest.fixture
def env(factory, segmenter):
    """Клиент с подменённым окружением. Возвращает и подставные зависимости."""
    fetcher = FakeFetcher()
    translator = FakeTranslator()

    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_fetcher] = lambda: fetcher
    app.dependency_overrides[get_segmenter] = lambda: segmenter
    app.dependency_overrides[get_translator] = lambda: translator

    yield TestClient(app), fetcher, translator

    app.dependency_overrides.clear()


# --- постановка в очередь ---


def test_post_accepts_and_runs(env):
    client, fetcher, translator = env

    r = client.post("/api/chapters", json={"url": URL})
    assert r.status_code == 202
    body = r.json()
    assert body["created"] is True
    assert body["status"] == ChapterStatus.FETCHING
    assert fetcher.calls == 1
    assert translator.calls == 1


def test_repeat_post_does_not_touch_network(env):
    """«За одной главой ходим один раз» — концепция §1.3."""
    client, fetcher, _ = env
    first = client.post("/api/chapters", json={"url": URL}).json()

    second = client.post("/api/chapters", json={"url": URL})

    assert second.status_code == 202
    assert second.json()["id"] == first["id"]
    assert second.json()["created"] is False
    assert fetcher.calls == 1, "повторный POST не должен ходить на сайт"


def test_repeat_post_after_failure_retries(env):
    """Для читателя повторный запрос после отказа и есть кнопка «ещё раз»."""
    client, fetcher, _ = env
    fetcher.failure = FetchFailure(ErrorKind.CHALLENGE, "челлендж")
    chapter_id = client.post("/api/chapters", json={"url": URL}).json()["id"]
    assert client.get(f"/api/chapters/{chapter_id}").json()["status"] == ChapterStatus.FAILED

    fetcher.failure = None
    client.post("/api/chapters", json={"url": URL})

    assert fetcher.calls == 2
    got = client.get(f"/api/chapters/{chapter_id}").json()
    assert got["status"] == ChapterStatus.READY
    assert got["error"] is None


def test_two_chapters_share_one_document(env, factory):
    """Главы одной книги не должны плодить книги: у documents своего ключа нет."""
    client, _, _ = env
    client.post("/api/chapters", json={"url": URL})
    client.post("/api/chapters", json={"url": OTHER_URL})

    with factory() as session:
        docs = {c.document_id for c in session.query(Chapter).all()}
    assert len(docs) == 1


@pytest.mark.parametrize("bad", ["ftp://example.com/1.html", "просто текст", ""])
def test_bad_url_rejected(env, bad):
    client, fetcher, _ = env
    r = client.post("/api/chapters", json={"url": bad})

    assert r.status_code == 422
    assert r.json()["error"]["kind"] == "bad_request"
    assert fetcher.calls == 0


# --- выдача главы ---


def test_get_returns_whole_chapter(env):
    client, _, _ = env
    chapter_id = client.post("/api/chapters", json={"url": URL}).json()["id"]

    got = client.get(f"/api/chapters/{chapter_id}").json()

    assert got["status"] == ChapterStatus.READY
    assert got["title"] == "Первая глава"
    assert got["content"].startswith("天很黑")
    assert got["error"] is None
    assert got["chars_sent"] > 0

    # Токены — то, по чему фронт строит спаны: [start, end, kind].
    assert got["tokens"] and all(len(t) == 3 for t in got["tokens"])
    assert got["tokens"][0][0] == 0
    assert got["tokens"][-1][1] == len(got["content"])

    # Офсеты предложений режут тот же канон, что приехал в content.
    assert got["sentences"]
    for s in got["sentences"]:
        assert got["content"][s["start"] : s["end"]].strip()
        assert s["translation"].startswith("пер:")


def test_get_unknown_chapter_404(env):
    client, _, _ = env
    r = client.get("/api/chapters/999")

    assert r.status_code == 404
    assert r.json()["error"]["kind"] == ErrorKind.NOT_FOUND


def test_readable_before_translation(env):
    """Глава без переводов — читаема: это состояние, а не отказ."""
    client, _, translator = env
    translator.failure = TranslateFailure("провайдер молчит")
    chapter_id = client.post("/api/chapters", json={"url": URL}).json()["id"]

    got = client.get(f"/api/chapters/{chapter_id}").json()

    assert got["status"] == ChapterStatus.SEGMENTED
    assert got["error"]["kind"] == ErrorKind.TRANSLATE_FAILED
    assert got["content"] and got["tokens"] and got["sentences"]
    assert all(s["translation"] is None for s in got["sentences"])


# --- отказы, каждый различим ---


@pytest.mark.parametrize(
    "kind",
    [
        ErrorKind.CHALLENGE,
        ErrorKind.NOT_FOUND,
        ErrorKind.FETCH_TIMEOUT,
        ErrorKind.ADAPTER_ERROR,
    ],
)
def test_fetch_error_kinds_are_visible(env, kind):
    """Пользователь должен видеть «челлендж» и «404» как разные состояния."""
    client, fetcher, _ = env
    fetcher.failure = FetchFailure(kind, "подробность")
    chapter_id = client.post("/api/chapters", json={"url": URL}).json()["id"]

    got = client.get(f"/api/chapters/{chapter_id}").json()

    assert got["status"] == ChapterStatus.FAILED
    assert got["error"]["kind"] == kind
    assert got["error"]["message"] == "подробность"
    assert got["content"] is None


def test_empty_extract_visible(env):
    """Оглавление вместо главы приходит с HTTP 200 — ловит только адаптер."""
    client, fetcher, _ = env
    fetcher.html = "<html><body><p>тут главы нет</p></body></html>"
    chapter_id = client.post("/api/chapters", json={"url": URL}).json()["id"]

    got = client.get(f"/api/chapters/{chapter_id}").json()

    assert got["status"] == ChapterStatus.FAILED
    assert got["error"]["kind"] == ErrorKind.EMPTY_EXTRACT


# --- дозалив перевода ---


def test_retranslate_fills_missing(env):
    client, _, translator = env
    translator.failure = TranslateFailure("отказ")
    chapter_id = client.post("/api/chapters", json={"url": URL}).json()["id"]

    translator.failure = None
    r = client.post(f"/api/chapters/{chapter_id}/translate")

    assert r.status_code == 202
    got = client.get(f"/api/chapters/{chapter_id}").json()
    assert got["status"] == ChapterStatus.READY
    assert got["error"] is None
    assert all(s["translation"] for s in got["sentences"])


def test_retranslate_does_not_resend_done(env):
    client, _, translator = env
    chapter_id = client.post("/api/chapters", json={"url": URL}).json()["id"]
    assert translator.calls == 1

    client.post(f"/api/chapters/{chapter_id}/translate")

    assert translator.calls == 1, "переводить было нечего"


def test_retranslate_unknown_chapter_404(env):
    client, _, _ = env
    r = client.post("/api/chapters/999/translate")
    assert r.status_code == 404
    assert r.json()["error"]["kind"] == ErrorKind.NOT_FOUND


def test_retranslate_without_text_conflicts(env):
    """Текста нет — переводить нечего, и это не ошибка переводчика."""
    client, fetcher, _ = env
    fetcher.failure = FetchFailure(ErrorKind.CHALLENGE, "челлендж")
    chapter_id = client.post("/api/chapters", json={"url": URL}).json()["id"]

    r = client.post(f"/api/chapters/{chapter_id}/translate")

    assert r.status_code == 409
    assert r.json()["error"]["kind"] == ErrorKind.EMPTY_EXTRACT


def test_retranslate_without_translator_is_503(env, factory, segmenter):
    client, _, _ = env
    chapter_id = client.post("/api/chapters", json={"url": URL}).json()["id"]

    app.dependency_overrides[get_translator] = lambda: None
    r = client.post(f"/api/chapters/{chapter_id}/translate")

    assert r.status_code == 503
    assert r.json()["error"]["kind"] == ErrorKind.TRANSLATE_FAILED


def test_pipeline_without_translator_stops_at_segmented(env):
    """Ключа нет — глава всё равно читается, просто без переводов."""
    client, _, _ = env
    app.dependency_overrides[get_translator] = lambda: None

    chapter_id = client.post("/api/chapters", json={"url": URL}).json()["id"]

    got = client.get(f"/api/chapters/{chapter_id}").json()
    assert got["status"] == ChapterStatus.SEGMENTED
    assert got["error"] is None
    assert got["content"]


# --- условный запрос ---
#
# Пока идёт перевод, клиент опрашивает статус каждые полторы секунды, а глава
# весит под сотню килобайт. Метка версии превращает этот опрос в 304 без тела:
# содержимое клиент достаёт из своего кэша, а по сети едут заголовки.
#
# Ошибка здесь опаснее, чем кажется: слишком «стабильная» метка означает, что
# читатель смотрит на «перевожу…» уже поверх готовой главы и не узнает об этом,
# пока не перезагрузит страницу руками.


def test_response_carries_an_etag(env):
    client, _, _ = env
    chapter_id = client.post("/api/chapters", json={"url": URL}).json()["id"]

    r = client.get(f"/api/chapters/{chapter_id}")

    assert r.status_code == 200
    assert r.headers["etag"]
    # Ревалидация обязана идти каждый раз: содержимое меняется по ходу работы.
    assert r.headers["cache-control"] == "no-cache"


def test_unchanged_chapter_answers_304_without_body(env):
    client, _, _ = env
    chapter_id = client.post("/api/chapters", json={"url": URL}).json()["id"]
    etag = client.get(f"/api/chapters/{chapter_id}").headers["etag"]

    again = client.get(f"/api/chapters/{chapter_id}", headers={"if-none-match": etag})

    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["etag"] == etag


def test_stale_etag_gets_the_whole_chapter(env):
    client, _, _ = env
    chapter_id = client.post("/api/chapters", json={"url": URL}).json()["id"]

    # Значение заголовка обязано быть ASCII — это требование самого HTTP.
    r = client.get(f"/api/chapters/{chapter_id}", headers={"if-none-match": '"stale"'})

    assert r.status_code == 200
    assert r.json()["content"]


def test_etag_changes_when_translation_arrives(env, factory):
    """Переводы живут в sentences, и строка главы при этом не меняется вовсе."""
    client, _, translator = env
    translator.failure = TranslateFailure("отказ")
    chapter_id = client.post("/api/chapters", json={"url": URL}).json()["id"]
    before = client.get(f"/api/chapters/{chapter_id}").headers["etag"]

    translator.failure = None
    client.post(f"/api/chapters/{chapter_id}/translate")

    assert client.get(f"/api/chapters/{chapter_id}").headers["etag"] != before


def test_etag_changes_when_next_chapter_is_loaded(env, factory):
    """`next_chapter_id` — факт о соседней главе, а не свойство этой."""
    from app.db.models import Chapter as ChapterModel

    client, _, _ = env
    chapter_id = client.post("/api/chapters", json={"url": URL}).json()["id"]
    with factory() as session:
        session.get(ChapterModel, chapter_id).next_chapter_url = OTHER_URL
        session.commit()

    before = client.get(f"/api/chapters/{chapter_id}").headers["etag"]
    client.post("/api/chapters", json={"url": OTHER_URL})

    assert client.get(f"/api/chapters/{chapter_id}").headers["etag"] != before


def test_different_chapters_have_different_etags(env):
    client, _, _ = env
    first = client.post("/api/chapters", json={"url": URL}).json()["id"]
    second = client.post("/api/chapters", json={"url": OTHER_URL}).json()["id"]

    assert (
        client.get(f"/api/chapters/{first}").headers["etag"]
        != client.get(f"/api/chapters/{second}").headers["etag"]
    )


def test_etag_is_stable_between_identical_requests(env):
    """Иначе 304 не случится никогда и вся затея бессмысленна."""
    client, _, _ = env
    chapter_id = client.post("/api/chapters", json={"url": URL}).json()["id"]

    first = client.get(f"/api/chapters/{chapter_id}").headers["etag"]
    second = client.get(f"/api/chapters/{chapter_id}").headers["etag"]

    assert first == second

"""Личный словарь: сохранение с контекстом, правка, удаление.

Проверяется прежде всего то, ради чего таблица устроена именно так: повторная
встреча слова не плодит карточки, а карточка переживает удаление главы. Второе
особенно важно — контекст хранит копию предложения ровно для этого случая.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_session_factory
from app.db.base import Base
from app.db.models import Chapter, Context, Document, Sentence, Source, UserWord
from app.domain import ChapterStatus, ErrorKind
from app.main import app
from app.services.words import ContextInput, WordError, save_word

SENTENCE = "他站起来走到窗户旁边，看见外面下着大雨。"
WORD = "窗户"
# Офсеты контекста обязаны резать предложение обратно в само слово.
WORD_AT = SENTENCE.index(WORD)


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'words.db'}")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):
        dbapi_connection.cursor().execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


@pytest.fixture
def session(factory):
    with factory() as s:
        yield s


@pytest.fixture
def client(factory):
    app.dependency_overrides[get_session_factory] = lambda: factory
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def chapter(session) -> Chapter:
    src = Source(kind="web", site="51shucheng.net", lang="zh")
    doc = Document(source=src, key="https://51shucheng.net/renwen/kniga/", title="книга", lang="zh")
    ch = Chapter(
        document=doc,
        url="https://51shucheng.net/renwen/kniga/1.html",
        status=ChapterStatus.READY,
        content=SENTENCE,
    )
    ch.sentences.append(Sentence(idx=0, start_offset=0, end_offset=len(SENTENCE)))
    session.add(ch)
    session.commit()
    return ch


def _context(chapter: Chapter | None = None) -> ContextInput:
    return ContextInput(
        sentence=SENTENCE,
        offset_start=WORD_AT,
        offset_end=WORD_AT + len(WORD),
        chapter_id=chapter.id if chapter else None,
        sentence_id=chapter.sentences[0].id if chapter else None,
    )


# --- сохранение ---


def test_saves_word_with_context(session, chapter):
    word, created = save_word(session, headword="窗户", context=_context(chapter))

    assert created
    assert word.headword == "窗户"
    assert len(word.contexts) == 1
    assert word.contexts[0].sentence == SENTENCE
    assert word.contexts[0].chapter_id == chapter.id


def test_second_save_adds_context_not_duplicate(session, chapter):
    """Имя героя встречается сотнями раз — карточка должна остаться одна."""
    save_word(session, headword="窗户", context=_context(chapter))
    other = ContextInput(sentence="窗户在另一个句子里出现。", offset_start=0, offset_end=2)
    word, created = save_word(session, headword="窗户", context=other)

    assert not created
    assert session.query(UserWord).count() == 1
    assert len(word.contexts) == 2


def test_identical_context_is_not_stored_twice(session, chapter):
    save_word(session, headword="窗户", context=_context(chapter))
    save_word(session, headword="窗户", context=_context(chapter))

    assert session.query(Context).count() == 1


def test_context_survives_chapter_deletion(session, chapter):
    """Ради этого контекст и хранит копию предложения (RFC §7)."""
    word, _ = save_word(session, headword="窗户", context=_context(chapter))
    session.delete(chapter)
    session.commit()
    session.expire_all()

    kept = session.get(Context, word.contexts[0].id)
    assert kept is not None
    assert kept.sentence == SENTENCE
    assert kept.chapter_id is None
    assert kept.sentence_id is None


def test_deleting_word_removes_its_contexts(session, chapter):
    word, _ = save_word(session, headword="窗户", context=_context(chapter))
    session.delete(word)
    session.commit()

    assert session.query(Context).count() == 0


def test_word_without_context(session):
    word, created = save_word(session, headword="窗户")
    assert created and word.contexts == []


def test_reading_is_not_overwritten(session):
    """Своё чтение читателя важнее словарного: молча затирать его нельзя."""
    save_word(session, headword="窗户", reading="chuāng hu")
    word, _ = save_word(session, headword="窗户", reading="другое")
    assert word.reading == "chuāng hu"


def test_own_fields_are_updated_on_resave(session):
    save_word(session, headword="窗户", user_translation="окно")
    word, _ = save_word(session, headword="窗户", user_translation="окошко")
    assert word.user_translation == "окошко"


@pytest.mark.parametrize(
    "context",
    [
        ContextInput(sentence="короткое", offset_start=5, offset_end=2),
        ContextInput(sentence="короткое", offset_start=0, offset_end=999),
    ],
)
def test_broken_offsets_rejected(session, context):
    """Офсеты, не режущие предложение, сделали бы контекст бессмысленным."""
    with pytest.raises(WordError):
        save_word(session, headword="窗户", context=context)


def test_offsets_must_cut_the_word(session):
    """Сдвиг на одно слово сохранился бы молча и всплыл в карточке через месяц.

    Выглядело бы это как ошибка разметки главы, а не как испорченная запись, —
    и чинили бы не то.
    """
    shifted = ContextInput(sentence=SENTENCE, offset_start=0, offset_end=len(WORD))
    with pytest.raises(WordError, match="офсеты режут"):
        save_word(session, headword=WORD, context=shifted)


def test_empty_headword_rejected(session):
    with pytest.raises(WordError):
        save_word(session, headword="   ")


def test_unknown_chapter_rejected(session):
    """Иначе внешний ключ сработает внутри базы и наружу уйдёт пятисотка."""
    context = ContextInput(
        sentence=SENTENCE,
        offset_start=WORD_AT,
        offset_end=WORD_AT + len(WORD),
        chapter_id=999,
    )
    with pytest.raises(WordError, match="главы 999 нет"):
        save_word(session, headword=WORD, context=context)


# --- ручки ---


def test_api_create_and_list(client, chapter):
    r = client.post(
        "/api/words",
        json={
            "headword": "窗户",
            "reading": "chuāng hu",
            "context": {
                "sentence": SENTENCE,
                "offset_start": WORD_AT,
                "offset_end": WORD_AT + len(WORD),
                "chapter_id": chapter.id,
            },
        },
    )
    assert r.status_code == 201
    assert r.json()["headword"] == "窗户"
    assert len(r.json()["contexts"]) == 1

    page = client.get("/api/words").json()
    assert page["total"] == 1
    assert page["items"][0]["reading"] == "chuāng hu"


def test_api_search(client):
    client.post("/api/words", json={"headword": "窗户", "user_translation": "окно"})
    client.post("/api/words", json={"headword": "大雨", "user_translation": "ливень"})

    found = client.get("/api/words", params={"query": "окно"}).json()
    assert [i["headword"] for i in found["items"]] == ["窗户"]


def test_api_patch_own_fields(client):
    word_id = client.post("/api/words", json={"headword": "窗户"}).json()["id"]

    r = client.patch(f"/api/words/{word_id}", json={"user_translation": "окно", "note": "из главы"})

    assert r.status_code == 200
    assert r.json()["user_translation"] == "окно"
    assert r.json()["note"] == "из главы"


def test_api_patch_empty_string_clears(client):
    """Пустая строка стирает поле, а пропущенное поле не трогает."""
    word_id = client.post(
        "/api/words", json={"headword": "窗户", "user_translation": "окно", "note": "заметка"}
    ).json()["id"]

    body = client.patch(f"/api/words/{word_id}", json={"user_translation": ""}).json()

    assert body["user_translation"] is None
    assert body["note"] == "заметка"


def test_api_delete(client):
    word_id = client.post("/api/words", json={"headword": "窗户"}).json()["id"]

    assert client.delete(f"/api/words/{word_id}").status_code == 204
    assert client.get("/api/words").json()["total"] == 0


def test_api_unknown_word_404(client):
    assert client.patch("/api/words/999", json={"note": "x"}).status_code == 404
    r = client.delete("/api/words/999")
    assert r.status_code == 404
    assert r.json()["error"]["kind"] == ErrorKind.NOT_FOUND


def test_api_broken_context_is_422(client):
    r = client.post(
        "/api/words",
        json={
            "headword": "窗户",
            "context": {"sentence": "короткое", "offset_start": 0, "offset_end": 999},
        },
    )
    assert r.status_code == 422

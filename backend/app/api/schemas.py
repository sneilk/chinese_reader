"""Формы запросов и ответов API (RFC §8).

`GET /api/chapters/{id}` отдаёт главу целиком — текст, токены, предложения с
переводами. Для главы в 3000 иероглифов это сотни килобайт, что для одного
пользователя нормально и избавляет фронт от запроса на каждое предложение.
"""

from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from app.db.models import Chapter, UserWord
from app.domain import ChapterStatus


class ChapterCreate(BaseModel):
    url: str = Field(min_length=8, max_length=1024)

    @field_validator("url")
    @classmethod
    def _http_only(cls, value: str) -> str:
        value = value.strip()
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("нужен http- или https-адрес главы")
        return value


class ErrorOut(BaseModel):
    """Отказ в том виде, в каком его показывают пользователю."""

    kind: str
    message: str = ""


class ChapterAccepted(BaseModel):
    """Ответ на постановку главы в очередь: клиент дальше опрашивает статус."""

    id: int
    status: ChapterStatus
    created: bool


class SentenceOut(BaseModel):
    # `id` нужен, чтобы сохранённое слово ссылалось на конкретное предложение,
    # а не только на его текст (contexts.sentence_id).
    id: int
    idx: int
    start: int
    end: int
    translation: str | None = None


class DictEntryOut(BaseModel):
    headword: str
    traditional: str | None = None
    reading: str | None = None
    senses: list[str] = []
    source: str


class CharGlossOut(BaseModel):
    """Значение одного знака — для слова, которого в словаре нет."""

    char: str
    reading: str | None = None
    senses: list[str] = []


class LookupOut(BaseModel):
    word: str
    found: bool
    # Карточка собрана из знаков, а не из статьи о слове целиком.
    approximate: bool
    entries: list[DictEntryOut] = []
    chars: list[CharGlossOut] = []


class ContextIn(BaseModel):
    """Предложение, в котором слово встретилось, вместе с офсетами внутри него."""

    sentence: str = Field(min_length=1, max_length=4000)
    offset_start: int = Field(ge=0)
    offset_end: int = Field(ge=0)
    chapter_id: int | None = None
    sentence_id: int | None = None


class WordCreate(BaseModel):
    headword: str = Field(min_length=1, max_length=64)
    lang: str = Field(default="zh", max_length=8)
    reading: str | None = Field(default=None, max_length=128)
    user_translation: str | None = None
    note: str | None = None
    context: ContextIn | None = None


class WordUpdate(BaseModel):
    """Правка своих полей. Пропущенное поле не трогаем, пустая строка стирает."""

    reading: str | None = Field(default=None, max_length=128)
    user_translation: str | None = None
    note: str | None = None


class ContextOut(BaseModel):
    sentence: str
    offset_start: int
    offset_end: int
    chapter_id: int | None = None
    sentence_id: int | None = None
    created_at: datetime


class WordOut(BaseModel):
    id: int
    lang: str
    headword: str
    reading: str | None = None
    user_translation: str | None = None
    note: str | None = None
    added_at: datetime
    contexts: list[ContextOut] = []

    @classmethod
    def of(cls, word: UserWord) -> WordOut:
        return cls(
            id=word.id,
            lang=word.lang,
            headword=word.headword,
            reading=word.reading,
            user_translation=word.user_translation,
            note=word.note,
            added_at=word.added_at,
            contexts=[
                ContextOut(
                    sentence=c.sentence,
                    offset_start=c.offset_start,
                    offset_end=c.offset_end,
                    chapter_id=c.chapter_id,
                    sentence_id=c.sentence_id,
                    created_at=c.created_at,
                )
                for c in word.contexts
            ],
        )


class WordsPage(BaseModel):
    items: list[WordOut]
    total: int


class ChapterOut(BaseModel):
    id: int
    url: str
    title: str | None = None
    status: ChapterStatus
    error: ErrorOut | None = None
    content: str | None = None
    tokens: list[tuple[int, int, str]] = []
    sentences: list[SentenceOut] = []
    chars_sent: int = 0

    @classmethod
    def of(cls, chapter: Chapter) -> ChapterOut:
        return cls(
            id=chapter.id,
            url=chapter.url,
            title=chapter.title,
            status=ChapterStatus(chapter.status),
            error=(
                ErrorOut(kind=chapter.error_kind, message=chapter.error_detail or "")
                if chapter.error_kind
                else None
            ),
            content=chapter.content,
            tokens=json.loads(chapter.tokens_json) if chapter.tokens_json else [],
            sentences=[
                SentenceOut(
                    id=s.id,
                    idx=s.idx,
                    start=s.start_offset,
                    end=s.end_offset,
                    translation=s.translation,
                )
                for s in chapter.sentences
            ],
            chars_sent=chapter.chars_sent,
        )

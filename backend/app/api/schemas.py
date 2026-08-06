"""Формы запросов и ответов API (RFC §8).

`GET /api/chapters/{id}` отдаёт главу целиком — текст, токены, предложения с
переводами. Для главы в 3000 иероглифов это сотни килобайт, что для одного
пользователя нормально и избавляет фронт от запроса на каждое предложение.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from app.db.models import Chapter
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
    idx: int
    start: int
    end: int
    translation: str | None = None


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
                    idx=s.idx,
                    start=s.start_offset,
                    end=s.end_offset,
                    translation=s.translation,
                )
                for s in chapter.sentences
            ],
            chars_sent=chapter.chars_sent,
        )

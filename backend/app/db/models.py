"""Модели MVP: источник → книга → глава → предложение.

Схема из RFC §7. Два осознанных отступления от наброска:

* `sentences.start` / `end` названы `start_offset` / `end_offset` — `end`
  является ключевым словом SQL, и хотя SQLAlchemy его экранирует, ручные
  запросы к базе превращаются в возню с кавычками;
* `status` хранится строкой с CHECK-ограничением, а не отдельным типом:
  SQLite всё равно не имеет ENUM, а миграции остаются читаемыми.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain import ChapterStatus, ErrorKind

__all__ = [
    "Chapter",
    "ChapterStatus",
    "DictEntry",
    "Document",
    "ErrorKind",
    "Sentence",
    "Source",
    "TranslationUsage",
]


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime, server_default=func.now(), nullable=False)


class Source(Base):
    """Сайт или файл, откуда пришёл текст."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # web | epub | txt
    site: Mapped[str | None] = mapped_column(String(255))
    base_url: Mapped[str | None] = mapped_column(String(1024))
    title: Mapped[str | None] = mapped_column(String(512))
    author: Mapped[str | None] = mapped_column(String(255))
    lang: Mapped[str] = mapped_column(String(8), nullable=False, default="zh")
    created_at: Mapped[datetime] = _created()

    documents: Mapped[list[Document]] = relationship(back_populates="source")

    __table_args__ = (CheckConstraint("kind in ('web','epub','txt')", name="ck_sources_kind"),)


class Document(Base):
    """Книга. В MVP заводится по одной главе, но глава без книги не бывает."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    title: Mapped[str | None] = mapped_column(String(512))
    lang: Mapped[str] = mapped_column(String(8), nullable=False, default="zh")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    source: Mapped[Source] = relationship(back_populates="documents")
    chapters: Mapped[list[Chapter]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Chapter(Base):
    """Глава: и хранилище, и кэш сайта — разница только в происхождении content."""

    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    idx: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String(512))
    # Уникальность URL — то, что делает повторный POST /api/chapters бесплатным
    # и не пускает нас на сайт второй раз за той же главой.
    url: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    # Канон: нормализованный текст. Офсеты токенов и предложений — по нему.
    content: Mapped[str | None] = mapped_column(Text)
    tokens_json: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ChapterStatus.FETCHING)
    error_kind: Mapped[str | None] = mapped_column(String(32))
    error_detail: Mapped[str | None] = mapped_column(Text)
    chars_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    fetched_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    document: Mapped[Document] = relationship(back_populates="chapters")
    sentences: Mapped[list[Sentence]] = relationship(
        back_populates="chapter", cascade="all, delete-orphan", order_by="Sentence.idx"
    )

    __table_args__ = (
        CheckConstraint(
            "status in ('fetching','segmented','translating','ready','failed')",
            name="ck_chapters_status",
        ),
        Index("ix_chapters_document_idx", "document_id", "idx"),
    )


class Sentence(Base):
    """Предложение главы вместе с переводом.

    Не кэш редких обращений, а нормальные данные: глава переводится целиком
    при загрузке (translation.md §3).
    """

    __tablename__ = "sentences"

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"))
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)

    translation: Mapped[str | None] = mapped_column(Text)
    # Глоссарий влияет на результат: без этого поля правка словаря молча
    # не применится к уже переведённой главе (translation.md §5).
    glossary_hash: Mapped[str | None] = mapped_column(String(64))
    translated_at: Mapped[datetime | None] = mapped_column(DateTime)

    chapter: Mapped[Chapter] = relationship(back_populates="sentences")

    __table_args__ = (
        UniqueConstraint("chapter_id", "idx", name="uq_sentences_chapter_idx"),
        CheckConstraint("end_offset >= start_offset", name="ck_sentences_offsets"),
    )


class TranslationUsage(Base):
    """Журнал расходов на перевод: одна строка — один запрос к провайдеру.

    Нужен ровно затем, что просит translation.md §7: посчитать, во сколько
    обходится месяц чтения, и не дать счёту улететь (RFC §6). Считать по
    `chapters.chars_sent` нельзя — там накопительный итог главы без даты, а
    тарификация идёт за календарный месяц.

    Пишется по подтверждённому ответу провайдера — только там известно точное
    число отправленных символов. Значит при обрыве уже после отправки учёт
    занижен; потолок мягкий, и на пару запросов это допустимо.
    """

    __tablename__ = "translation_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    chars_sent: Mapped[int] = mapped_column(Integer, nullable=False)
    sentences: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chapter_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created()

    __table_args__ = (Index("ix_translation_usage_created", "created_at"),)


class DictEntry(Base):
    """Словарная статья.

    Один ряд — одно значение заголовка в одном источнике. У иероглифического
    слова легко бывает несколько статей (разные чтения, разные источники),
    поэтому уникальности по headword нет.

    `reading` — пиньинь с диакритикой для показа, `reading_numbered` — исходная
    форма с цифрами тона: по ней удобно искать и сравнивать.
    """

    __tablename__ = "dict_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    lang: Mapped[str] = mapped_column(String(8), nullable=False, default="zh")
    headword: Mapped[str] = mapped_column(String(64), nullable=False)
    traditional: Mapped[str | None] = mapped_column(String(64))
    reading: Mapped[str | None] = mapped_column(String(128))
    reading_numbered: Mapped[str | None] = mapped_column(String(128))
    pos: Mapped[str | None] = mapped_column(String(32))
    senses_json: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    freq: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index("ix_dict_entries_lookup", "lang", "headword"),
        Index("ix_dict_entries_source", "source"),
    )

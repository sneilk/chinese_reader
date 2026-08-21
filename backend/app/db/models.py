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
from app.domain import ChapterStatus, ErrorKind, Language

__all__ = [
    "Chapter",
    "ChapterStatus",
    "Context",
    "DictEntry",
    "Document",
    "ErrorKind",
    "Sentence",
    "Source",
    "SpeechUsage",
    "TranslationUsage",
    "UserWord",
    "WordOccurrence",
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
    # Язык оригинала. Лежит на главе, а не только на книге, потому что
    # известен он **после** загрузки: адаптер сайта его объявляет, а
    # generic-фолбэк и вовсе определяет по тексту.
    lang: Mapped[str] = mapped_column(String(8), nullable=False, default=Language.ZH)
    # Адрес следующей главы книги, как его дал адаптер. Это не пагинация
    # внутри главы (её конвейер склеивает молча), а вход в обход книги: у
    # novelarrow оглавления в разметке нет, и других способов идти вперёд тоже.
    next_chapter_url: Mapped[str | None] = mapped_column(String(1024))
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
        CheckConstraint("lang in ('zh','en')", name="ck_chapters_lang"),
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


class UserWord(Base):
    """Слово из личного словаря.

    Уникально по паре (язык, заголовок): одно слово — одна карточка, сколько
    бы раз читатель на него ни наткнулся. Повторное сохранение добавляет
    контекст, а не заводит дубль.

    `user_translation` и `note` — свои поля читателя, и они важнее словарных:
    именно правленый перевод попадёт в глоссарий переводчика (T0.6 показал,
    что в глоссарий должны идти только слова с заведомо неверным дефолтным
    переводом, а не весь словарь).

    `status`, `due_at`, `ease` — задел под интервальные повторения. В MVP не
    используются и в UI не показываются; заведены сразу, чтобы не мигрировать
    таблицу со словами ради трёх колонок.
    """

    __tablename__ = "user_words"

    id: Mapped[int] = mapped_column(primary_key=True)
    lang: Mapped[str] = mapped_column(String(8), nullable=False, default="zh")
    headword: Mapped[str] = mapped_column(String(64), nullable=False)
    reading: Mapped[str | None] = mapped_column(String(128))

    user_translation: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="new")
    added_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime)
    ease: Mapped[int | None] = mapped_column(Integer)

    contexts: Mapped[list[Context]] = relationship(
        back_populates="user_word",
        cascade="all, delete-orphan",
        order_by="Context.created_at",
    )

    __table_args__ = (UniqueConstraint("lang", "headword", name="uq_user_words_lang_headword"),)


class Context(Base):
    """Предложение, в котором слово встретилось.

    `sentence` хранит текст копией намеренно (RFC §7): карточка должна
    пережить удаление главы. По той же причине ссылки на главу и предложение
    обнуляются, а не удаляют контекст следом за ними — иначе смысл копии
    терялся бы ровно в тот момент, когда она нужна.
    """

    __tablename__ = "contexts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_word_id: Mapped[int] = mapped_column(
        ForeignKey("user_words.id", ondelete="CASCADE"), nullable=False
    )
    chapter_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id", ondelete="SET NULL"))
    sentence_id: Mapped[int | None] = mapped_column(ForeignKey("sentences.id", ondelete="SET NULL"))

    sentence: Mapped[str] = mapped_column(Text, nullable=False)
    # Офсеты слова внутри `sentence`, а не внутри главы: глава может исчезнуть.
    offset_start: Mapped[int] = mapped_column(Integer, nullable=False)
    offset_end: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = _created()

    user_word: Mapped[UserWord] = relationship(back_populates="contexts")

    __table_args__ = (
        CheckConstraint("offset_end >= offset_start", name="ck_contexts_offsets"),
        Index("ix_contexts_user_word", "user_word_id"),
    )


class WordOccurrence(Base):
    """Сколько раз слово встретилось в главе.

    Агрегат вместо таблицы токенов (segmentation.md §6): книга дала бы сотни
    тысяч строк, а вопросы к ним всего два — «где ещё встречалось это слово»
    и «сколько раз». Наполняется при разборе главы.
    """

    __tablename__ = "word_occurrences"

    headword: Mapped[str] = mapped_column(String(64), primary_key=True)
    chapter_id: Mapped[int] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), primary_key=True
    )
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (Index("ix_word_occurrences_headword", "headword"),)


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


class SpeechUsage(Base):
    """Журнал расходов на озвучку: одна строка — один синтез.

    Отдельная таблица, а не строки в `translation_usage`, по единственной
    причине: тариф другой. Сложив символы перевода и символы синтеза в одну
    сумму, мы получили бы число, которое не соответствует ни одному счёту, и
    месячный потолок стал бы бессмысленным для обоих.

    Пишется только на **новый** синтез. Повторное прослушивание берёт mp3 из
    кэша на диске и в журнал не попадает — иначе расход рос бы от чтения, а
    не от денег.
    """

    __tablename__ = "speech_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    voice: Mapped[str] = mapped_column(String(32), nullable=False)
    chars_sent: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created()

    __table_args__ = (Index("ix_speech_usage_created", "created_at"),)


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

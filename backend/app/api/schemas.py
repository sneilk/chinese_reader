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

from app.config import settings
from app.db.models import Chapter, UserWord
from app.domain import ChapterStatus, Language
from app.services.books import MAX_TITLE_CHARS, BookRow
from app.services.walks import Walk


def require_http_url(value: str) -> str:
    """Адрес страницы, а не что попало. Общий для всех ручек, берущих ссылку."""
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("нужен http- или https-адрес страницы")
    return value


class ChapterCreate(BaseModel):
    url: str = Field(min_length=8, max_length=1024)
    #: Сколько ещё глав пройти вперёд по ссылке «следующая глава». Ноль —
    #: обычная загрузка одной главы, и это значение по умолчанию: обход книги
    #: должен быть решением читателя, а не побочным действием ссылки.
    follow: int = Field(default=0, ge=0, le=settings.max_chapters_per_run)

    @field_validator("url")
    @classmethod
    def _http_only(cls, value: str) -> str:
        return require_http_url(value)


class ErrorOut(BaseModel):
    """Отказ в том виде, в каком его показывают пользователю."""

    kind: str
    message: str = ""


class ChapterAccepted(BaseModel):
    """Ответ на постановку главы в очередь: клиент дальше опрашивает статус."""

    id: int
    status: ChapterStatus
    created: bool


class BookOut(BaseModel):
    """Книга в списке.

    Заголовок здесь только тот, что написал читатель: вывести его из страницы
    неоткуда (см. services/books.py). `None` означает «показывать адрес», и
    именно так книга и выглядит, пока её не назвали.
    """

    id: int
    #: Адрес книги на сайте. Как показать его человеку, решает интерфейс.
    key: str
    title: str | None = None
    lang: Language
    site: str | None = None
    chapters: int
    #: Сколько глав уже можно открыть и читать.
    readable: int

    @classmethod
    def of(cls, book: BookRow) -> BookOut:
        return cls(
            id=book.id,
            key=book.key,
            title=book.title,
            lang=Language(book.lang),
            site=book.site,
            chapters=book.chapters,
            readable=book.readable,
        )


class BookUpdate(BaseModel):
    """Правка книги. Пока правится только название — больше и нечего.

    Значение по умолчанию отсутствует, и это не строгость ради строгости.
    Пустая строка означает «стереть название и показывать адрес» — осмысленное
    действие, а не «поля не передали». Будь у поля умолчание `None`, эти два
    случая слились бы в один, и `PATCH {}` молча стирал бы заголовок.
    """

    title: str | None = Field(max_length=MAX_TITLE_CHARS)


class BookWalkStart(BaseModel):
    """Запуск выгрузки книги целиком."""

    #: Переводить ли главы по ходу выгрузки. По умолчанию нет, и это не
    #: осторожность, а арифметика: 550 глав — это полтора миллиона символов,
    #: половина месячного потолка. Текст читаем и без перевода, а перевести
    #: главу можно кнопкой, когда до неё дойдёт очередь.
    translate: bool = False


class BookWalkOut(BaseModel):
    """Состояние выгрузки книги: по нему экран показывает прогресс."""

    book_id: int
    running: bool
    #: Сколько глав загружено с начала этой выгрузки.
    loaded: int
    #: Потолок этого запуска.
    limit: int
    #: Отказ, оборвавший выгрузку. `None` — дошли до конца книги или до потолка.
    stopped_by: str | None = None
    #: Выгрузку попросили прекратить. Это не отказ: загруженное на месте, и
    #: продолжить можно той же кнопкой.
    cancelled: bool = False

    @classmethod
    def of(cls, walk: Walk) -> BookWalkOut:
        return cls(
            book_id=walk.book_id,
            running=walk.running,
            loaded=walk.loaded,
            limit=walk.limit,
            stopped_by=walk.stopped_by,
            cancelled=walk.cancelled,
        )

    @classmethod
    def idle(cls, book_id: int) -> BookWalkOut:
        """Книгу ещё не выгружали. Это состояние, а не отсутствие ответа."""
        return cls(book_id=book_id, running=False, loaded=0, limit=0)


class ChapterBrief(BaseModel):
    """Глава в оглавлении: всё, что нужно, чтобы выбрать и открыть.

    Ни текста, ни токенов: список из двадцати глав с их содержимым весил бы
    больше, чем сама книга, а нужен он ради одного нажатия.
    """

    id: int
    #: Место в цепочке; `None` — глава загружена отдельной ссылкой в середину.
    idx: int | None = None
    title: str | None = None
    lang: Language
    status: ChapterStatus
    error: ErrorOut | None = None

    @classmethod
    def of(cls, chapter: Chapter) -> ChapterBrief:
        return cls(
            id=chapter.id,
            idx=chapter.idx,
            title=chapter.title,
            lang=Language(chapter.lang),
            status=ChapterStatus(chapter.status),
            error=(
                ErrorOut(kind=chapter.error_kind, message=chapter.error_detail or "")
                if chapter.error_kind
                else None
            ),
        )


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
    # Форма, под которой слово нашлось: `running` найдено как `run`. `None` —
    # совпало как есть. Показывать обязательно: иначе карточка выглядит так,
    # будто в словаре стоит ровно то, что в тексте.
    matched: str | None = None
    entries: list[DictEntryOut] = []
    chars: list[CharGlossOut] = []


class DiagnosticsOut(BaseModel):
    """Состояние сервиса. Ключей и адресов здесь нет — только факт настройки."""

    version: str
    schema_revision: str | None = None
    db_size_bytes: int
    chapters: int
    sentences: int
    user_words: int
    dict_entries: int
    dict_sources: dict[str, int] = {}
    userdict_words: int
    translator_configured: bool
    chars_this_month: int
    month_limit: int
    speech_configured: bool
    speech_voice: str
    speech_chars_this_month: int
    speech_month_limit: int
    tts_cache_bytes: int
    browser_profile_exists: bool
    browser_headless: bool


class SpeechCheckOut(BaseModel):
    """Итог живой проверки синтеза: единственное здесь, что нельзя узнать чтением."""

    ok: bool
    kind: str | None = None
    detail: str = ""


class BrowserCheckIn(BaseModel):
    """Открыть страницу в видимом окне и подождать, пока проверку пройдут."""

    url: str = Field(min_length=8, max_length=1024)
    #: Сколько держать окно открытым. Ждём не вслепую: как только челленджа не
    #: стало, ответ приходит сразу, — поэтому обычный случай стоит секунды.
    seconds: float = Field(
        default=60.0, ge=0, le=settings.browser_check_timeout_seconds
    )

    @field_validator("url")
    @classmethod
    def _http_only(cls, value: str) -> str:
        return require_http_url(value)


class BrowserCheckOut(BaseModel):
    """Что оказалось на странице к концу ожидания."""

    ok: bool
    #: Причина отказа, если страница так и не отдалась. `None` — всё в порядке.
    kind: str | None = None
    status: int
    title: str = ""
    #: Адрес, на котором браузер в итоге оказался: редиректы бывают говорящими.
    url: str = ""
    waited_seconds: float = 0.0
    #: Видно ли окно человеку. False — браузер headless, и проходить нечего.
    visible: bool = True
    #: Есть ли снимок экрана. По нему и видно, что там за проверка.
    screenshot: bool = False


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
    # Язык оригинала: от него зависит и шрифт текста, и язык запроса к
    # словарю. Фронт не должен угадывать его по содержимому.
    lang: Language = Language.ZH
    status: ChapterStatus
    error: ErrorOut | None = None
    content: str | None = None
    tokens: list[tuple[int, int, str]] = []
    sentences: list[SentenceOut] = []
    chars_sent: int = 0
    #: Адрес следующей главы на сайте — есть он или нет, решает адаптер.
    next_url: str | None = None
    #: Она же, если уже загружена: тогда переход бесплатный, без похода на сайт.
    next_chapter_id: int | None = None

    @classmethod
    def of(cls, chapter: Chapter, next_chapter_id: int | None = None) -> ChapterOut:
        return cls(
            id=chapter.id,
            url=chapter.url,
            title=chapter.title,
            lang=Language(chapter.lang),
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
            next_url=chapter.next_chapter_url,
            next_chapter_id=next_chapter_id,
        )

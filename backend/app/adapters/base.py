"""Интерфейс адаптеров сайтов.

Адаптер отвечает на один вопрос: «эта страница — глава, и если да, то какой
у неё заголовок и текст». Всё остальное — задача загрузчика и конвейера.

Важное разделение обязанностей, найденное в T1.3: загрузчик видит только
HTTP-уровень, поэтому «мягкий 404» (несуществующий номер главы отдаёт 200 и
страницу оглавления) может распознать **только адаптер**.

## Две ссылки вперёд, и это разные ссылки

`next_url` — следующая **страница той же главы**: конвейер склеит её с
текущей в одну запись, потому что офсеты обязаны считаться по главе целиком
(pipeline.fetch_pages).

`next_chapter_url` — следующая **глава книги**: отдельная запись, отдельный
URL, отдельный конвейер. Путать их нельзя: склеенные главы дали бы одну
запись на всю книгу, а разрезанная по страницам глава — предложения,
обрывающиеся на границе страницы.

## Порог «текста здесь нет» — величина языковая

100 иероглифов и 100 слов — это примерно один и тот же объём смысла, но
считать их одной функцией нельзя: в английской главе иероглифов ноль, и общий
порог по `han` завернул бы любую английскую страницу как пустую.
"""

import re
from dataclasses import dataclass, field
from typing import Protocol

from app.domain import ErrorKind, Language

_HAN = re.compile(r"[一-鿿]")
_WORD = re.compile(r"[A-Za-z]+")

# Ниже этого порога считаем, что текста главы на странице нет. Реальная глава
# на 51shucheng — около 3000 иероглифов, самая короткая мыслимая — сотни.
MIN_CHAPTER_HAN = 100
# То же для латиницы. Английская глава веб-новеллы — 1500–4000 слов; сотня
# слов на странице означает аннотацию, шапку или заглушку, но не главу.
MIN_CHAPTER_WORDS = 100


def han_count(text: str) -> int:
    return len(_HAN.findall(text or ""))


def word_count(text: str) -> int:
    return len(_WORD.findall(text or ""))


def detect_language(text: str) -> Language:
    """Определить язык куска текста. Нужен generic-адаптеру: он берёт всё.

    Решает наличие иероглифов, а не их доля: в китайской главе латиница
    попадается (имена, звукоподражания), а в английской иероглифов не бывает
    вовсе. Порог в десяток знаков отсекает случайную цитату.
    """
    return Language.ZH if han_count(text) >= 10 else Language.EN


@dataclass(frozen=True)
class ChapterRaw:
    """Извлечённая глава до нормализации и сегментации."""

    title: str
    paragraphs: list[str] = field(default_factory=list)
    lang: Language = Language.ZH
    # Пагинации внутри главы на 51shucheng нет (T0.4), но интерфейс её
    # допускает: следующий сайт может вести себя иначе.
    next_url: str | None = None
    # Ссылка на следующую главу книги. Оглавления у novelarrow из HTML не
    # достать (sources.md §2), поэтому книга обходится именно по ней.
    next_chapter_url: str | None = None

    @property
    def han(self) -> int:
        return sum(han_count(p) for p in self.paragraphs)

    @property
    def size(self) -> int:
        """Объём текста в единицах своего языка: иероглифы или слова."""
        counter = han_count if self.lang is Language.ZH else word_count
        return sum(counter(p) for p in self.paragraphs)

    @property
    def min_size(self) -> int:
        return MIN_CHAPTER_HAN if self.lang is Language.ZH else MIN_CHAPTER_WORDS

    @property
    def units(self) -> str:
        """Как называются единицы объёма — для текста отказа."""
        return "иероглифов" if self.lang is Language.ZH else "слов"


class AdapterFailure(Exception):
    """Страница загрузилась, но главы в ней нет."""

    def __init__(self, kind: ErrorKind, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}" if detail else str(kind))
        self.kind = kind
        self.detail = detail


def require_text(chapter: ChapterRaw, where: str) -> ChapterRaw:
    """Проверить, что текста набралось на главу. Иначе — `empty_extract`."""
    if chapter.size < chapter.min_size:
        raise AdapterFailure(
            ErrorKind.EMPTY_EXTRACT,
            f"{where}: текста {chapter.size} {chapter.units}, "
            f"порог {chapter.min_size}",
        )
    return chapter


class SiteAdapter(Protocol):
    name: str
    #: Язык оригинала сайта. `None` — «как получится», решает содержимое
    #: страницы; так устроен только generic-фолбэк.
    lang: Language | None

    def matches(self, url: str) -> bool: ...

    def parse_chapter(self, html: str, url: str) -> ChapterRaw: ...

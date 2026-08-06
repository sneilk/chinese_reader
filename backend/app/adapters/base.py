"""Интерфейс адаптеров сайтов.

Адаптер отвечает на один вопрос: «эта страница — глава, и если да, то какой
у неё заголовок и текст». Всё остальное — задача загрузчика и конвейера.

Важное разделение обязанностей, найденное в T1.3: загрузчик видит только
HTTP-уровень, поэтому «мягкий 404» (несуществующий номер главы отдаёт 200 и
страницу оглавления) может распознать **только адаптер**.
"""

import re
from dataclasses import dataclass, field
from typing import Protocol

from app.domain import ErrorKind

_HAN = re.compile(r"[一-鿿]")

# Ниже этого порога считаем, что текста главы на странице нет. Реальная глава
# на 51shucheng — около 3000 иероглифов, самая короткая мыслимая — сотни.
MIN_CHAPTER_HAN = 100


def han_count(text: str) -> int:
    return len(_HAN.findall(text or ""))


@dataclass(frozen=True)
class ChapterRaw:
    """Извлечённая глава до нормализации и сегментации."""

    title: str
    paragraphs: list[str] = field(default_factory=list)
    # Пагинации внутри главы на 51shucheng нет (T0.4), но интерфейс её
    # допускает: следующий сайт может вести себя иначе.
    next_url: str | None = None

    @property
    def han(self) -> int:
        return sum(han_count(p) for p in self.paragraphs)


class AdapterFailure(Exception):
    """Страница загрузилась, но главы в ней нет."""

    def __init__(self, kind: ErrorKind, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}" if detail else str(kind))
        self.kind = kind
        self.detail = detail


class SiteAdapter(Protocol):
    name: str

    def matches(self, url: str) -> bool: ...

    def parse_chapter(self, html: str, url: str) -> ChapterRaw: ...

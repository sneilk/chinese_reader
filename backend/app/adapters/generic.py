"""Запасной адаптер для сайтов, под которые нет своего.

Годится, чтобы что-то показать на случайной ссылке, но не годится как основной
путь: на 51shucheng generic-извлечение притащило бы список глав, а это ~40%
лишних символов, за которые платит переводчик (T0.5).

Язык здесь не объявлен, а определяется по тексту: адаптер берёт любой адрес,
а значит и любой язык. Ошибиться тут дёшево — по наличию иероглифов эти два
языка не путаются.
"""

import trafilatura
from lxml import html as lh

from app.adapters.base import AdapterFailure, ChapterRaw, detect_language, require_text
from app.adapters.dom import find_next_link
from app.domain import ErrorKind


class GenericAdapter:
    name = "generic"
    lang = None  # решает содержимое страницы

    def matches(self, url: str) -> bool:
        return True  # последний в очереди, берёт всё оставшееся

    def parse_chapter(self, html: str, url: str) -> ChapterRaw:
        text = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
        if not text:
            raise AdapterFailure(ErrorKind.EMPTY_EXTRACT, "trafilatura не нашла основной текст")

        paragraphs = [" ".join(line.split()) for line in text.splitlines()]
        paragraphs = [p for p in paragraphs if p]

        meta = trafilatura.extract_metadata(html)
        title = (meta.title if meta and meta.title else "").strip()

        chapter = ChapterRaw(
            title=title,
            paragraphs=paragraphs,
            lang=detect_language("\n".join(paragraphs)),
            next_chapter_url=self._next_chapter(html, url),
        )
        return require_text(chapter, "извлечено мало текста")

    @staticmethod
    def _next_chapter(html: str, url: str) -> str | None:
        """Ссылка вперёд ищется в исходном HTML: trafilatura навигацию срезает."""
        try:
            return find_next_link(lh.fromstring(html), exclude=url)
        except Exception:  # noqa: BLE001 — битая разметка не отменяет главу
            return None

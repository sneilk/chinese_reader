"""Запасной адаптер для сайтов, под которые нет своего.

Годится, чтобы что-то показать на случайной ссылке, но не годится как основной
путь: на 51shucheng generic-извлечение притащило бы список глав, а это ~40%
лишних символов, за которые платит переводчик (T0.5).
"""

import trafilatura

from app.adapters.base import MIN_CHAPTER_HAN, AdapterFailure, ChapterRaw
from app.domain import ErrorKind


class GenericAdapter:
    name = "generic"

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

        chapter = ChapterRaw(title=title, paragraphs=paragraphs)
        if chapter.han < MIN_CHAPTER_HAN:
            raise AdapterFailure(
                ErrorKind.EMPTY_EXTRACT,
                f"извлечено всего {chapter.han} иероглифов",
            )
        return chapter

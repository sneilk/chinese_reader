"""Склейка пагинированной главы.

На 51shucheng пагинации внутри главы нет (T0.4), поэтому проверяется всё на
подставном адаптере: реального сайта с такой разметкой у нас под рукой нет, а
поведение должно быть определено до того, как он появится.

Главное здесь — что при упоре в потолок глава падает с ошибкой, а не
обрезается молча: глава без конца выглядит как испорченный текст, и искать
причину читатель будет не там.
"""

import pytest

from app.adapters.base import AdapterFailure, ChapterRaw
from app.config import settings
from app.domain import ErrorKind
from app.fetchers.base import FetchResult
from app.services.pipeline import fetch_pages

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class PagedFetcher:
    """Отдаёт номер страницы в HTML; адаптер ниже читает его оттуда."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    async def get(self, url: str) -> FetchResult:
        self.urls.append(url)
        return FetchResult(url=url, status=200, html=url, title="глава")


class PagedAdapter:
    """Глава из `pages` страниц: каждая ссылается на следующую."""

    name = "paged"

    def __init__(self, pages: int, *, loop: bool = False) -> None:
        self.pages = pages
        self.loop = loop

    def matches(self, url: str) -> bool:
        return True

    def parse_chapter(self, html: str, url: str) -> ChapterRaw:
        number = int(url.rsplit("/", 1)[-1])
        if self.loop:
            # Кольцо: вторая страница отправляет обратно на первую.
            next_url = "https://site/1" if number == 2 else "https://site/2"
        else:
            next_url = f"https://site/{number + 1}" if number < self.pages else None
        return ChapterRaw(
            title=f"глава, страница {number}",
            paragraphs=[f"абзац {number}.1", f"абзац {number}.2"],
            next_url=next_url,
        )


async def test_single_page_is_untouched():
    fetcher = PagedFetcher()
    raw = await fetch_pages(fetcher, "https://site/1", PagedAdapter(pages=1))

    assert raw.paragraphs == ["абзац 1.1", "абзац 1.2"]
    assert fetcher.urls == ["https://site/1"]


async def test_two_pages_glued_into_one():
    fetcher = PagedFetcher()
    raw = await fetch_pages(fetcher, "https://site/1", PagedAdapter(pages=2))

    assert raw.paragraphs == ["абзац 1.1", "абзац 1.2", "абзац 2.1", "абзац 2.2"]
    assert fetcher.urls == ["https://site/1", "https://site/2"]
    # Заголовок берём с первой страницы: на остальных он обычно с номером.
    assert raw.title == "глава, страница 1"


async def test_stops_at_page_limit():
    fetcher = PagedFetcher()
    with pytest.raises(AdapterFailure) as failure:
        await fetch_pages(fetcher, "https://site/1", PagedAdapter(pages=999))

    assert failure.value.kind is ErrorKind.ADAPTER_ERROR
    assert str(settings.max_pages_per_chapter) in failure.value.detail
    assert len(fetcher.urls) == settings.max_pages_per_chapter


async def test_loop_does_not_hang():
    """Кольцо «следующая → предыдущая» встречается на живых сайтах."""
    fetcher = PagedFetcher()
    raw = await fetch_pages(fetcher, "https://site/1", PagedAdapter(pages=2, loop=True))

    assert fetcher.urls == ["https://site/1", "https://site/2"]
    assert raw.paragraphs == ["абзац 1.1", "абзац 1.2", "абзац 2.1", "абзац 2.2"]


async def test_relative_next_url_resolved():
    """Относительная ссылка считается от того адреса, где мы оказались."""

    class RelativeAdapter:
        name = "relative"

        def matches(self, url: str) -> bool:
            return True

        def parse_chapter(self, html: str, url: str) -> ChapterRaw:
            second = url.endswith("_2.html")
            return ChapterRaw(
                title="глава",
                paragraphs=["текст"],
                next_url=None if second else "chapter_2.html",
            )

    fetcher = PagedFetcher()
    await fetch_pages(fetcher, "https://site/book/chapter.html", RelativeAdapter())

    assert fetcher.urls == [
        "https://site/book/chapter.html",
        "https://site/book/chapter_2.html",
    ]

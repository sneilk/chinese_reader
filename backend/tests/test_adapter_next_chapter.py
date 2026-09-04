"""Разбор страницы главы: у каждого источника обязан находиться вход в книгу.

Тест сквозной по источникам, и это его смысл. Отдельные тесты адаптеров
проверяют, что со страницы снят правильный текст; здесь проверяется другое —
что со страницы снят **адрес следующей главы**. Без него книга состоит из
одной главы: `pipeline.walk_chapters` останавливается на первом же шаге, а в
читалке вместо перехода висит «Ссылки на следующую главу на странице не
нашлось».

Ровно так и было. У 51shucheng кнопка «下一章» стоит на каждой странице главы и
несёт абсолютный адрес, но адаптер её никогда не читал: `next_chapter_url`
китайской главы был пуст всегда. У novelarrow ссылку искали, но не там —
живая кнопка вперёд оказалась голой стрелкой в `<svg>` без единого символа
текста, с подписью только в `aria-label`.

Отсюда два прогона одного набора проверок.

**Структурные фикстуры** едут в git и гоняются всегда. Разметка навигации в
них снята с живых страниц дословно, вплоть до идентификаторов и подписей:
именно она здесь и проверяется, а текст — заполнитель.

**Живые снимки** лежат в `data/fixtures/` вне git (чужие произведения) и
снимаются `scripts/snapshot_fixtures.py`. Их прогон пропускается, если файлов
нет, и в этом всё дело: рукописная фикстура показывает то, что мы думаем о
разметке сайта, а живая — то, что сайт отдаёт на самом деле. Разошлись они уже
дважды.

Точный адрес живой страницы никто не обещает — сайт волен перенумеровать
главы, — поэтому от живого снимка спрашиваются свойства, а не значение:
ссылка есть, ведёт не на саму себя и остаётся внутри той же книги.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pytest
from lxml import html as lh

from app.adapters.base import ChapterRaw
from app.adapters.registry import pick_adapter
from app.services.chapters import book_prefix

FIXTURES = Path(__file__).parent / "fixtures"
#: Живые снимки лежат в боевом каталоге данных, а не во временном, который
#: подкладывает conftest: снимаются они раз в полгода и общие на репозиторий.
LIVE = Path(__file__).resolve().parents[1] / "data" / "fixtures"


@dataclass(frozen=True)
class Site:
    """Источник в том виде, в каком его видит разбор страницы."""

    #: Имя адаптера, который обязан взять этот адрес.
    adapter: str
    #: Адрес страницы главы. От него считаются относительные ссылки.
    url: str
    #: Структурная фикстура — в git.
    fixture: str
    #: Чего ждём от неё: адрес следующей главы, как он записан в разметке.
    next_chapter: str
    #: Живой снимок в `data/fixtures/` — вне git, может отсутствовать.
    #: `None` — снимать нечего: generic берёт любой сайт и своего у него нет.
    live: str | None


SITES = [
    Site(
        adapter="51shucheng",
        url="https://www.51shucheng.net/renwen/test-book/1.html",
        fixture="shucheng_chapter.html",
        next_chapter="/renwen/test-book/2.html",
        live="51shucheng-chapter.html",
    ),
    Site(
        adapter="novelarrow",
        url="https://novelarrow.com/novel/the-long-cartography/chapter-12",
        fixture="novelarrow_chapter.html",
        next_chapter="/novel/the-long-cartography/chapter-13",
        live="novelarrow-chapter.html",
    ),
    Site(
        adapter="generic",
        url="https://example.com/story/chapter-4",
        fixture="generic_chapter.html",
        next_chapter="/story/chapter-5",
        live=None,
    ),
]

IDS = [site.adapter for site in SITES]


def parse(html: str, url: str) -> ChapterRaw:
    return pick_adapter(url).parse_chapter(html, url)


def canonical_url(html: str) -> str | None:
    """Адрес, под которым страница считает саму себя.

    Своего адреса у снимка нет: он лежит файлом. А брать вместо него адрес
    структурной фикстуры нельзя — относительные ссылки считаются от него, и
    книга получилась бы «та же» по построению, что и проверять незачем. Оба
    сайта объявляют `rel="canonical"`, и это ровно то, что нужно.
    """
    found = lh.fromstring(html).xpath("//link[@rel='canonical']/@href")
    return str(found[0]) if found else None


@pytest.fixture(params=SITES, ids=IDS)
def site(request) -> Site:
    return request.param


@pytest.fixture
def chapter(site: Site) -> ChapterRaw:
    return parse((FIXTURES / site.fixture).read_text(encoding="utf-8"), site.url)


@pytest.fixture
def live_page(site: Site) -> tuple[str, ChapterRaw]:
    """Живой снимок вместе с его собственным адресом."""
    if site.live is None:
        pytest.skip("у generic-адаптера нет своего сайта — снимать нечего")

    path = LIVE / site.live
    if not path.exists():
        pytest.skip(f"живого снимка нет: {path.name}, снимается scripts/snapshot_fixtures.py")

    html = path.read_text(encoding="utf-8")
    url = canonical_url(html)
    if url is None:
        pytest.skip(f"{path.name}: страница не объявила своего адреса, сверять не с чем")
    return url, parse(html, url)


# --- структурные фикстуры: гоняются всегда ---


def test_the_right_adapter_takes_the_page(site: Site) -> None:
    assert pick_adapter(site.url).name == site.adapter


def test_page_yields_a_chapter(chapter: ChapterRaw) -> None:
    """Ссылка вперёд не должна доставаться ценой текста: сначала глава."""
    assert chapter.title
    assert chapter.paragraphs
    assert chapter.size >= chapter.min_size


def test_next_chapter_is_found(site: Site, chapter: ChapterRaw) -> None:
    """Главное: со страницы виден вход в следующую главу."""
    assert chapter.next_chapter_url == site.next_chapter


def test_next_chapter_is_not_this_one(site: Site, chapter: ChapterRaw) -> None:
    """Ссылка на саму себя — кольцо: обход остановится, не сделав ни шага."""
    assert urljoin(site.url, chapter.next_chapter_url) != site.url


def test_next_chapter_is_not_the_previous_one(chapter: ChapterRaw) -> None:
    """У пары кнопок разметка общая, и перепутать их — значит поехать назад."""
    assert "chapter-11" not in (chapter.next_chapter_url or "")
    assert "chapter-3" not in (chapter.next_chapter_url or "")
    assert "/0.html" not in (chapter.next_chapter_url or "")


def test_page_pagination_stays_empty(chapter: ChapterRaw) -> None:
    """Следующая страница главы и следующая глава — разные поля и разный смысл.

    Спутать их дорого в обе стороны: склеенные главы дали бы одну запись на всю
    книгу, а разрезанная по страницам глава — предложения, обрывающиеся на
    границе страницы.
    """
    assert chapter.next_url is None


# --- живые снимки: пропускаются, если их не снимали ---


def test_live_page_yields_a_chapter(live_page: tuple[str, ChapterRaw]) -> None:
    _, chapter = live_page
    assert chapter.title
    assert chapter.paragraphs


def test_live_page_has_a_way_forward(live_page: tuple[str, ChapterRaw]) -> None:
    """То, ради чего снимок и снимался: на живой разметке ссылка тоже видна."""
    _, chapter = live_page
    assert chapter.next_chapter_url, "на живой странице не нашлось ссылки вперёд"


def test_live_next_chapter_stays_in_the_same_book(live_page: tuple[str, ChapterRaw]) -> None:
    """Точного адреса никто не обещает, а вот книга обязана остаться той же.

    Ссылка «наверх», в оглавление, — самый вероятный способ ошибиться: она
    стоит в том же блоке навигации и на последней главе занимает место
    «следующей». Обход по ней ушёл бы не вперёд, а в страницу без текста.
    """
    url, chapter = live_page
    absolute = urljoin(url, chapter.next_chapter_url)

    assert urlparse(absolute).hostname == urlparse(url).hostname
    assert absolute != url
    assert book_prefix(absolute) == book_prefix(url)


def test_live_link_really_leads_to_the_next_chapter() -> None:
    """Ссылка ведёт туда, где лежит следующая глава, — а не «куда-то».

    Всё остальное про живую страницу проверяется по одной: есть ссылка,
    похожа на адрес главы той же книги. Похожа — не значит ведёт. Убедиться в
    этом можно только имея на руках обе страницы: адрес, снятый с первой,
    обязан совпасть с собственным адресом второй, а у второй, в свою очередь,
    обязан найтись свой шаг вперёд — иначе цепочка обрывается на втором звене.
    """
    pages = [LIVE / "51shucheng-chapter.html", LIVE / "51shucheng-chapter-next.html"]
    if not all(path.exists() for path in pages):
        pytest.skip("нужны оба снимка подряд, снимаются scripts/snapshot_fixtures.py")

    first_html, second_html = (path.read_text(encoding="utf-8") for path in pages)
    first_url, second_url = (canonical_url(first_html), canonical_url(second_html))
    if first_url is None or second_url is None:
        pytest.skip("страницы не объявили своих адресов, сверять не с чем")

    first = parse(first_html, first_url)
    second = parse(second_html, second_url)

    assert urljoin(first_url, first.next_chapter_url) == second_url
    assert second.next_chapter_url, "цепочка обязана продолжаться и со второй главы"
    assert second.title != first.title, "это соседние главы, а не одна и та же"


# --- конец книги ---


def test_last_chapter_of_a_shucheng_book_has_no_way_forward() -> None:
    """На последней главе `#BookNext` ведёт в оглавление, а не вперёд.

    Отличить это от настоящей ссылки можно ровно одним признаком: адрес главы
    у сайта всегда `/{жанр}/{книга}/{номер}.html`, а адрес оглавления — нет.
    Пойти по нему значило бы записать оглавление книги в её главы.
    """
    html = (FIXTURES / "shucheng_chapter.html").read_text(encoding="utf-8")
    ended = html.replace('href="/renwen/test-book/2.html"', 'href="/renwen/test-book"')

    got = parse(ended, SITES[0].url)

    assert got.next_chapter_url is None
    assert got.paragraphs, "конец книги — это не отказ разбора"

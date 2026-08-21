"""Адаптер novelarrow и структурный разбор страницы.

Тут проверяется не «эти селекторы работают», а то, что адаптер обходится
**без** селекторов. Живой фикстуры у нас нет (sources.md §2), и когда она
появится, класс контейнера всё равно окажется сгенерированным сборкой — то
есть временным. Поэтому фикстура синтетическая, а тесты — про свойства:
берётся самый плотный по абзацам блок, шапка и подвал в главу не попадают,
ссылка вперёд отличается от ссылки назад.

Отдельно проверяется порог пустоты. У китайского адаптера он в иероглифах,
и на английской странице показал бы ноль — то есть завернул бы любую годную
главу как пустую.
"""

from pathlib import Path

import pytest
from lxml import html as lh

from app.adapters.base import AdapterFailure, detect_language
from app.adapters.dom import densest_block, find_next_link, page_title
from app.adapters.novelarrow import NovelarrowAdapter
from app.domain import ErrorKind, Language

FIXTURE = Path(__file__).parent / "fixtures" / "novelarrow_chapter.html"
CHAPTER_URL = "https://novelarrow.com/novel/the-long-cartography/chapter-12"

adapter = NovelarrowAdapter()


@pytest.fixture(scope="module")
def html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def chapter(html):
    return adapter.parse_chapter(html, CHAPTER_URL)


# --- выбор адаптера ---


@pytest.mark.parametrize(
    "url",
    [
        "https://novelarrow.com/novel/x/chapter-1",
        "https://www.novelarrow.com/novel/x/chapter-1",
    ],
)
def test_matches_own_host(url):
    assert adapter.matches(url)


@pytest.mark.parametrize("url", ["https://51shucheng.net/x/1.html", "https://example.com/"])
def test_does_not_match_others(url):
    assert not adapter.matches(url)


def test_declares_english():
    assert adapter.lang is Language.EN


# --- текст главы ---


def test_title_from_h1(chapter):
    assert chapter.title == "Chapter 12: The Salt Road"


def test_paragraphs_extracted(chapter):
    assert len(chapter.paragraphs) == 7
    assert chapter.paragraphs[0].startswith("The road out of the lower town")


def test_navigation_is_not_in_the_text(chapter):
    """Шапка, подвал и «похожие книги» — это не глава."""
    joined = "\n".join(chapter.paragraphs)
    assert "You may also like" not in joined
    assert "respective authors" not in joined
    assert "Previous" not in joined


def test_scripts_are_dropped(chapter):
    assert all("__ads" not in p for p in chapter.paragraphs)


def test_paragraphs_are_single_lines(chapter):
    """Перенос строки внутри абзаца сломал бы канон: абзац равен строке."""
    assert all("\n" not in p for p in chapter.paragraphs)
    assert all(p == " ".join(p.split()) for p in chapter.paragraphs)


def test_language_is_english(chapter):
    assert chapter.lang is Language.EN
    assert chapter.size >= chapter.min_size
    assert chapter.han == 0, "иероглифов тут нет — порог по ним завернул бы главу"


# --- ссылка на следующую главу ---


def test_next_chapter_found(chapter):
    assert chapter.next_chapter_url == "/novel/the-long-cartography/chapter-13"


def test_next_is_not_previous(chapter):
    assert "chapter-11" not in (chapter.next_chapter_url or "")


def test_next_url_is_not_page_pagination(chapter):
    """Пагинация внутри главы и следующая глава — разные поля и разный смысл."""
    assert chapter.next_url is None


# --- отказы ---


def test_page_without_chapter_is_empty_extract():
    """Страница-заглушка отдаётся с HTTP 200 — распознать её может только адаптер."""
    stub = "<html><body><main><p>Sign in to continue reading.</p></main></body></html>"
    with pytest.raises(AdapterFailure) as e:
        adapter.parse_chapter(stub, CHAPTER_URL)
    assert e.value.kind is ErrorKind.EMPTY_EXTRACT


def test_short_page_reports_words_not_characters():
    stub = "<html><body><main><p>Too short to be a chapter.</p></main></body></html>"
    with pytest.raises(AdapterFailure) as e:
        adapter.parse_chapter(stub, CHAPTER_URL)
    assert "слов" in e.value.detail


# --- разбор разметки как таковой ---


def test_densest_block_prefers_the_deepest_holder(html):
    """`<body>` тоже содержит все абзацы — но вместе с шапкой и подвалом."""
    block = densest_block(lh.fromstring(html))
    assert block.get("id") == "chapter-body"


def test_next_link_prefers_rel_next():
    doc = lh.fromstring(
        '<html><body><a href="/late" class="next">Next</a>'
        '<link rel="next" href="/declared"></body></html>'
    )
    assert find_next_link(doc) == "/declared"


def test_next_link_by_text():
    doc = lh.fromstring('<html><body><a href="/n">Next Chapter &raquo;</a></body></html>')
    assert find_next_link(doc) == "/n"


def test_next_link_ignores_previous_by_class():
    doc = lh.fromstring('<html><body><a href="/p" class="btn prev">Back</a></body></html>')
    assert find_next_link(doc) is None


def test_next_link_skips_the_current_url():
    """Ссылка на саму себя — это кольцо, а не следующая глава."""
    doc = lh.fromstring('<html><body><a rel="next" href="/here">Next</a></body></html>')
    assert find_next_link(doc, exclude="/here") is None


def test_title_falls_back_to_head_title():
    doc = lh.fromstring(
        "<html><head><title>Chapter 3 - Book | Site</title></head><body></body></html>"
    )
    assert page_title(doc) == "Chapter 3 - Book"


def test_br_separated_text_becomes_paragraphs():
    """У ридеров встречается вёрстка переносами — иначе глава была бы одним абзацем."""
    doc = lh.fromstring(
        "<html><body><main><div><p>First line here.<br>Second line here.</p></div>"
        "</main></body></html>"
    )
    from app.adapters.dom import block_paragraphs

    assert block_paragraphs(densest_block(doc)) == ["First line here.", "Second line here."]


# --- определение языка ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The road was white with salt.", Language.EN),
        ("天很黑，风从窗户外面吹进来，屋子里没有一点声音。", Language.ZH),
        ("A name like 江雪明 in an English text.", Language.EN),
    ],
)
def test_detect_language(text, expected):
    """Решает наличие иероглифов: одна цитата китайского главу не перекрашивает."""
    assert detect_language(text) is expected

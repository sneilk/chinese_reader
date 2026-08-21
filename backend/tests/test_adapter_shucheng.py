"""Адаптер 51shucheng.

Тесты гоняются на структурной фикстуре: разметка настоящая, текст —
заполнитель. Живая глава является чужим произведением, в репозиторий не
кладётся и подхватывается из data/ только если лежит рядом.
"""

from pathlib import Path

import pytest

from app.adapters.base import AdapterFailure
from app.adapters.registry import pick_adapter
from app.adapters.shucheng import ShuchengAdapter
from app.domain import ErrorKind

FIXTURES = Path(__file__).parent / "fixtures"
REAL_CHAPTER = Path(__file__).resolve().parents[1] / "data" / "chapter-fixture.html"
REAL_INDEX = Path(__file__).resolve().parents[1] / "data" / "book-index-fixture.html"

CHAPTER_URL = "https://www.51shucheng.net/renwen/test-book/1.html"

adapter = ShuchengAdapter()


@pytest.fixture
def chapter():
    html = (FIXTURES / "shucheng_chapter.html").read_text(encoding="utf-8")
    return adapter.parse_chapter(html, CHAPTER_URL)


def test_title(chapter):
    assert chapter.title == "第一章 开始"


def test_paragraphs_collected(chapter):
    # В фикстуре 9 абзацев, один из них пустой.
    assert len(chapter.paragraphs) == 8
    assert all(p.strip() for p in chapter.paragraphs)


def test_navigation_is_not_in_text(chapter):
    """Список глав — ~40% иероглифов страницы, и платить за них не за что."""
    joined = "".join(chapter.paragraphs)
    for nav in ("第二章", "第三章", "章节目录", "评论"):
        assert nav not in joined


def test_ads_and_scripts_stripped(chapter):
    joined = "".join(chapter.paragraphs)
    assert "广告位" not in joined
    assert "tracker" not in joined
    assert "ad_slot" not in joined


def test_book_index_is_rejected():
    """Мягкий 404: страница оглавления не должна стать пустой главой."""
    html = (FIXTURES / "shucheng_book_index.html").read_text(encoding="utf-8")
    with pytest.raises(AdapterFailure) as e:
        adapter.parse_chapter(html, CHAPTER_URL)
    assert e.value.kind is ErrorKind.EMPTY_EXTRACT


def test_too_short_chapter_is_rejected():
    html = """<html><body><div id="neirong"><p>短。</p></div></body></html>"""
    with pytest.raises(AdapterFailure) as e:
        adapter.parse_chapter(html, CHAPTER_URL)
    assert e.value.kind is ErrorKind.EMPTY_EXTRACT


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.51shucheng.net/renwen/x/1.html", "51shucheng"),
        ("https://51shucheng.net/x/1.html", "51shucheng"),
        ("https://novelarrow.com/novel/x", "novelarrow"),
        ("https://example.com/", "generic"),
    ],
)
def test_registry_picks_adapter(url, expected):
    assert pick_adapter(url).name == expected


@pytest.mark.skipif(not REAL_CHAPTER.exists(), reason="живая фикстура лежит вне git")
def test_real_chapter():
    """Страховка от расхождения структурной фикстуры с реальностью."""
    got = adapter.parse_chapter(REAL_CHAPTER.read_text(encoding="utf-8"), CHAPTER_URL)
    assert got.title
    assert len(got.paragraphs) > 50
    # На живой странице 5424 иероглифа всего и 2922 в тексте главы: адаптер
    # обязан отсечь навигацию, а не перевести её вместе с текстом.
    assert 2000 < got.han < 3500


@pytest.mark.skipif(not REAL_INDEX.exists(), reason="живая фикстура лежит вне git")
def test_real_book_index_rejected():
    with pytest.raises(AdapterFailure) as e:
        adapter.parse_chapter(REAL_INDEX.read_text(encoding="utf-8"), CHAPTER_URL)
    assert e.value.kind is ErrorKind.EMPTY_EXTRACT

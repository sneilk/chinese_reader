"""Запасной адаптер: язык по содержимому и ссылка вперёд из сырой разметки.

У generic две особенности, которых нет ни у одного адаптера сайта, и обе
проверяются здесь.

**Язык он не объявляет, а определяет.** Свой адаптер знает язык своего сайта
заранее; этот берёт любой адрес, а значит и любой язык, и решать приходится по
тексту. Ошибка тут не косметическая: от языка зависит, чем резать текст на
токены и с какого языка переводить.

**Ссылку вперёд он ищет в исходном HTML, а не в извлечённом тексте.**
trafilatura навигацию срезает — в этом её работа, — поэтому искать «следующую
главу» в её выдаче бессмысленно: там её уже нет.

Страницы здесь свои и намеренно непохожи на 51shucheng: у того есть свой
адаптер и своя фикстура, а сюда попадает всё остальное.
"""

import pytest

from app.adapters.base import AdapterFailure, ChapterRaw, detect_language
from app.adapters.generic import GenericAdapter
from app.domain import ErrorKind, Language

adapter = GenericAdapter()

URL = "https://example.com/story/chapter-4"

_ZH = [
    "夜里下了很久的雨，屋檐上的水一直滴到天亮才停下来。",
    "他把灯芯挑亮了一些，又坐回到桌子前面，翻开那本没有名字的册子。",
    "册子里写的都是别人的事情，可每一页都像是在说他自己。",
    "外面的风把窗纸吹得响个不停，他却一点也没有觉得冷。",
    "第二天清早，院子里的水洼映着灰白的天，看上去比昨夜还要深一些。",
    "他站在门口看了很久，最后还是把那本册子放进了怀里。",
]

_EN = [
    "The rain kept on most of the night, and the water off the eaves did not stop "
    "until the sky had gone grey.",
    "He trimmed the wick a little brighter, sat back down at the table, and opened "
    "the book that had no name on it.",
    "Everything written in it belonged to other people, and every page of it read "
    "as though it were about him.",
    "The wind worried at the paper in the window all night long, and he never once "
    "felt cold.",
    "In the early morning the puddles in the yard held a flat grey sky, and looked "
    "deeper than they had the night before.",
    "He stood in the doorway a long while, and in the end he put the book inside "
    "his coat and went out.",
]


def page(paragraphs: list[str], *, nav: str = "", title: str = "Chapter Four") -> str:
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (
        f"<html><head><title>{title}</title></head><body>"
        f"<nav>{nav}</nav>"
        f"<article><h1>{title}</h1>{body}</article>"
        "</body></html>"
    )


# --- выбор адаптера ---


@pytest.mark.parametrize(
    "url",
    ["https://example.com/", "https://51shucheng.net/x/1.html", "https://novelarrow.com/n/1"],
)
def test_matches_anything(url):
    """Он последний в очереди и обязан согласиться на любой адрес."""
    assert adapter.matches(url)


def test_declares_no_language():
    """`None` означает «решает содержимое страницы», а не «неизвестно»."""
    assert adapter.lang is None


# --- язык по содержимому ---


def test_chinese_page_is_chinese():
    got = adapter.parse_chapter(page(_ZH), URL)
    assert got.lang is Language.ZH
    assert got.size >= got.min_size


def test_english_page_is_english():
    got = adapter.parse_chapter(page(_EN), URL)
    assert got.lang is Language.EN
    assert got.han == 0
    assert got.size >= got.min_size


def test_english_page_is_measured_in_words():
    """Порог в иероглифах завернул бы английскую главу как пустую."""
    got = adapter.parse_chapter(page(_EN), URL)
    assert got.units == "слов"
    assert got.min_size == 100


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Обычный текст без иероглифов вовсе.", Language.EN),
        ("天很黑，风从窗户外面吹进来。", Language.ZH),
        ("A single 字 inside an English sentence.", Language.EN),
        ("", Language.EN),
    ],
)
def test_language_detection_needs_more_than_a_quotation(text, expected):
    """Решает наличие иероглифов, а не доля: цитата главу не перекрашивает."""
    assert detect_language(text) is expected


# --- ссылка на следующую главу ---


def test_next_chapter_found_in_navigation():
    """trafilatura навигацию срезает — искать ссылку надо в исходном HTML."""
    nav = '<a href="/story/chapter-3">Previous</a><a href="/story/chapter-5">Next Chapter</a>'
    got = adapter.parse_chapter(page(_EN, nav=nav), URL)
    assert got.next_chapter_url == "/story/chapter-5"


def test_previous_link_is_not_taken_for_next():
    nav = '<a href="/story/chapter-3">Previous</a>'
    assert adapter.parse_chapter(page(_EN, nav=nav), URL).next_chapter_url is None


def test_no_navigation_means_no_link():
    assert adapter.parse_chapter(page(_ZH), URL).next_chapter_url is None


def test_page_pagination_is_left_alone():
    """Пагинация внутри главы и следующая глава — разные поля и разный смысл."""
    nav = '<a href="/story/chapter-5">Next Chapter</a>'
    assert adapter.parse_chapter(page(_EN, nav=nav), URL).next_url is None


def test_broken_markup_does_not_lose_the_chapter():
    """Ссылка вперёд — приятное дополнение; текст важнее её."""
    html = page(_EN, nav='<a href="/story/chapter-5">Next Chapter</a>')
    got = adapter.parse_chapter(html.replace("<html>", "<html><<<"), URL)
    assert got.paragraphs, "битая разметка не должна отменять главу"


# --- отказы ---


def test_page_without_text_is_empty_extract():
    with pytest.raises(AdapterFailure) as e:
        adapter.parse_chapter("<html><body><p>Ничего тут нет.</p></body></html>", URL)
    assert e.value.kind is ErrorKind.EMPTY_EXTRACT


def test_page_with_no_body_at_all():
    with pytest.raises(AdapterFailure) as e:
        adapter.parse_chapter("<html></html>", URL)
    assert e.value.kind is ErrorKind.EMPTY_EXTRACT


def test_short_chinese_page_reports_characters():
    """Единицы в сообщении об отказе — языковые: иначе оно вводит в заблуждение."""
    with pytest.raises(AdapterFailure) as e:
        adapter.parse_chapter(page(_ZH[:1]), URL)
    assert "иероглифов" in e.value.detail


def test_short_english_page_reports_words():
    with pytest.raises(AdapterFailure) as e:
        adapter.parse_chapter(page(_EN[:1]), URL)
    assert "слов" in e.value.detail


# --- размер главы считается по языку ---


def test_size_switches_units_with_language():
    zh = ChapterRaw(title="", paragraphs=["天很黑，风从窗户外面吹进来。"], lang=Language.ZH)
    en = ChapterRaw(title="", paragraphs=["The night was very dark indeed."], lang=Language.EN)

    assert zh.size == zh.han == 12
    assert en.size == 6 and en.han == 0

"""Разрезка английского текста на токены.

Главный инвариант тот же, что у китайской сегментации, и он строже, чем
кажется: **токены обязаны покрывать текст сплошь и без нахлёстов**. Фронт
собирает главу обратно из них (`ChapterText.tsx`), а не из исходной строки,
поэтому дырка в покрытии — это не «неточная разметка», а пропавший на экране
кусок текста. Проверяется он в каждом сюжетном тесте отдельной функцией, а не
одним тестом на всё: рвётся покрытие обычно на конкретном виде ввода.
"""

import pytest

from app.lang.segment import TokenKind
from app.lang.segment_en import segment


def covers(text: str) -> bool:
    """Токены тайлят строку: встык, без дырок, от нуля до конца."""
    tokens = segment(text)
    position = 0
    for token in tokens:
        if token.start != position or token.surface != text[token.start : token.end]:
            return False
        position = token.end
    return position == len(text) or (not text and not tokens)


def surfaces(text: str, *kinds: TokenKind) -> list[str]:
    wanted = set(kinds) or set(TokenKind)
    return [t.surface for t in segment(text) if t.kind in wanted]


# --- покрытие ---


@pytest.mark.parametrize(
    "text",
    [
        "",
        "one",
        "one two",
        "  leading and trailing  ",
        "punctuation, then — a dash; and 'quotes'.",
        "line one\nline two\n\nline four",
        "mixed 42 tokens, 3.15 and 1,000 too",
        "\n",
        "no-final-newline",
    ],
)
def test_coverage_is_continuous(text):
    assert covers(text)


# --- слова ---


def test_words_are_latin_tokens():
    assert surfaces("The lantern went out", TokenKind.LATIN) == [
        "The",
        "lantern",
        "went",
        "out",
    ]


def test_apostrophe_stays_inside_the_word():
    """`don't` — одно слово: `don`, `'`, `t` были бы тремя бессмыслицами."""
    assert surfaces("she didn't answer", TokenKind.LATIN) == ["she", "didn't", "answer"]


def test_typographic_apostrophe_too():
    assert surfaces("she didn’t answer", TokenKind.LATIN) == ["she", "didn’t", "answer"]


def test_hyphen_splits_the_word():
    """`well-known` в словаре не найти, а обе половины — находятся."""
    assert surfaces("a well-known face", TokenKind.LATIN) == ["a", "well", "known", "face"]


def test_lookup_key_is_lowercased():
    """Поиск в словаре идёт по нижнему регистру: `The` и `the` — одно слово."""
    first = segment("The road")[0]
    assert first.surface == "The"
    assert first.lookup_key == "the"


# --- числа ---


@pytest.mark.parametrize("number", ["42", "3.15", "1,000", "1,000.50"])
def test_numbers_are_one_token(number):
    """Число целиком, а не «три», «точка», «пятнадцать»."""
    assert surfaces(f"about {number} here", TokenKind.DIGIT) == [number]


# --- абзацы ---


def test_newline_is_its_own_token():
    """По нему фронт режет главу на абзацы — склеенный, он их бы потерял."""
    assert [t.surface for t in segment("first.\nsecond.") if t.surface == "\n"] == ["\n"]


def test_newline_never_glued_to_punctuation():
    tokens = segment("end.\nnext")
    assert all(t.surface == "\n" or "\n" not in t.surface for t in tokens)


def test_paragraph_count_matches_newlines():
    text = "one\ntwo\nthree"
    assert sum(1 for t in segment(text) if t.surface == "\n") == 2


# --- офсеты ---


def test_offsets_slice_back():
    text = "The wolves were running, and nobody followed."
    for token in segment(text):
        assert text[token.start : token.end] == token.surface


def test_spaces_are_kept_as_tokens():
    """Пробел, выпавший из разметки, — это пробел, выпавший с экрана."""
    assert "".join(t.surface for t in segment("a b")) == "a b"

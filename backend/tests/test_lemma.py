"""Начальные формы английского слова.

Проверяется не «правильная лемма», а свойство, на котором держится карточка:
**нужная форма присутствует в списке кандидатов**. Их перебирает `lookup` до
первого попадания в словарь, поэтому лишний неправильный вариант ничего не
стоит, а недостающий правильный означает «в словарях этого нет» на обычном
слове.

Отдельно проверяется порядок в одном-единственном месте — точная форма идёт
первой. От него зависит, что покажет карточка на `saw`: пилу из текста или
прошедшее время от `see`.
"""

import pytest

from app.lang.lemma import candidates


def has(word: str, expected: str) -> bool:
    return expected in candidates(word)


# --- порядок ---


def test_exact_form_comes_first():
    """`saw` — и пила, и прошедшее от `see`. Спрашивать словарь надо о пиле."""
    assert candidates("saw")[0] == "saw"
    assert has("saw", "see")


def test_lowercase_follows_the_original():
    assert candidates("Road")[:2] == ["Road", "road"]


def test_no_duplicates():
    result = candidates("running")
    assert len(result) == len(set(result))


# --- правильные формы ---


@pytest.mark.parametrize(
    ("form", "base"),
    [
        ("went", "go"),
        ("gone", "go"),
        ("were", "be"),
        ("been", "be"),
        ("children", "child"),
        ("wolves", "wolf"),
        ("feet", "foot"),
        ("thought", "think"),
        ("brought", "bring"),
        ("lain", "lie"),
    ],
)
def test_irregular_forms(form, base):
    assert has(form, base)


# --- суффиксы ---


@pytest.mark.parametrize(
    ("form", "base"),
    [
        ("lanterns", "lantern"),
        ("boxes", "box"),
        ("cities", "city"),
        ("watched", "watch"),
        ("hoped", "hope"),
        ("stopped", "stop"),
        ("carried", "carry"),
        ("running", "run"),
        ("hoping", "hope"),
        ("walking", "walk"),
        ("darkest", "dark"),
        ("colder", "cold"),
        ("quietly", "quiet"),
    ],
)
def test_suffix_forms(form, base):
    assert has(form, base)


def test_double_s_is_not_a_plural():
    """`glass` не должно превращаться в `glas`."""
    assert "glas" not in candidates("glass")


# --- апострофы ---


@pytest.mark.parametrize(
    ("form", "base"),
    [
        ("don't", "do"),
        ("can't", "can"),
        ("won't", "will"),
        ("didn't", "did"),
        ("world's", "world"),
        ("I'll", "will"),
    ],
)
def test_contractions(form, base):
    assert has(form, base)


def test_contraction_also_yields_the_verb_base():
    """`didn't` → `did` → `do`: суффиксные правила работают после апострофа."""
    assert has("didn't", "do")


# --- край ---


@pytest.mark.parametrize("word", ["", "   "])
def test_empty_gives_nothing(word):
    assert candidates(word) == []


def test_non_letters_are_left_alone():
    """Цифрам и пунктуации начальную форму искать негде."""
    assert candidates("1,000") == ["1,000"]


def test_short_words_are_not_stripped_to_nothing():
    assert all(len(c) >= 1 for c in candidates("is"))

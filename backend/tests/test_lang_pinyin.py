"""Пиньинь с цифрами тона → пиньинь с диакритикой.

Правило выбора буквы под знак — не косметика: `jiu3` и `hui4` различаются
только тем, на какую из двух гласных попадёт знак, и ошибка здесь читается
как другое слово.
"""

import pytest

from app.lang.pinyin import numbered_to_accented


@pytest.mark.parametrize(
    ("numbered", "accented"),
    [
        ("chuan2 tong3", "chuán tǒng"),
        ("hao3", "hǎo"),
        ("ma1 ma5", "mā ma"),  # пятый тон — без знака
        ("e2", "é"),
        ("a1", "ā"),
    ],
)
def test_basic_tones(numbered, accented):
    assert numbered_to_accented(numbered) == accented


@pytest.mark.parametrize(
    ("numbered", "accented"),
    [
        ("jiu3", "jiǔ"),  # iu — знак на второй гласной
        ("hui4", "huì"),  # ui — тоже на второй
        ("liu2", "liú"),
        ("shui3", "shuǐ"),
        ("guo2", "guó"),  # o важнее u
        ("xie4", "xiè"),  # e важнее i
        ("hao3 kan4", "hǎo kàn"),  # a важнее o
    ],
)
def test_vowel_priority(numbered, accented):
    assert numbered_to_accented(numbered) == accented


@pytest.mark.parametrize(
    ("numbered", "accented"),
    [
        ("lu:3", "lǚ"),  # CC-CEDICT пишет ü как u:
        ("nv3", "nǚ"),  # и как v
        ("lu:4 you2", "lǜ yóu"),
    ],
)
def test_umlaut(numbered, accented):
    assert numbered_to_accented(numbered) == accented


def test_capital_letters_preserved():
    """Имена собственные CC-CEDICT пишет с большой буквы."""
    assert numbered_to_accented("Bei3 jing1") == "Běi jīng"


def test_mixed_with_digits():
    """`11 Qu1` — заголовок из цифр и слога, такие в дампе есть."""
    assert numbered_to_accented("11 Qu1") == "11 Qū"


def test_toneless_syllable_passes_through():
    assert numbered_to_accented("OK") == "OK"


def test_syllable_without_markable_vowel():
    """`ng` и `hm` — гласной под знак нет, портить нечего."""
    assert numbered_to_accented("ng2") == "ng"


def test_empty():
    assert numbered_to_accented("") == ""

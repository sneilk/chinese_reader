"""Нормализация в канон.

Проверяем не «текст стал красивее», а инварианты, на которые опирается всё
остальное: абзац равен строке, невидимых символов нет, полноширинная
пунктуация цела.
"""

from app.lang.normalize import normalize, normalize_paragraph


def test_zero_width_removed():
    assert normalize_paragraph("他​说﻿话") == "他说话"


def test_nbsp_becomes_space():
    assert normalize_paragraph("a b") == "a b"


def test_ideographic_space_indent_stripped():
    """Красная строка в китайском наборе — два идеографических пробела."""
    assert normalize_paragraph("　　他走了。") == "他走了。"


def test_runs_of_spaces_collapse():
    assert normalize_paragraph("a   \t b") == "a b"


def test_fullwidth_punctuation_survives():
    """NFKC схлопнул бы её в ASCII, а она несёт границы предложений."""
    src = "他说：“好！”，然后走了。"
    assert normalize_paragraph(src) == src


def test_paragraph_is_a_line():
    canon = normalize(["первый", "  ", "второй", ""])
    assert canon == "первый\nвторой"
    assert canon.count("\n") == 1


def test_empty_input():
    assert normalize([]) == ""
    assert normalize(["", "   ", "　"]) == ""

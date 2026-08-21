"""Резка на предложения.

Главный инвариант: офсеты обязаны резать канон обратно в тот же текст. Если
он нарушен, разъедутся и карточки, и переводы, и подсветка — причём молча.

В тестах на терминаторы фразы намеренно длиннее восьми символов: иначе
срабатывает склейка коротких кусков и проверять становится нечего. Склейка
проверяется отдельно, короткими.
"""

import pytest

from app.domain import Language
from app.lang.normalize import normalize
from app.lang.sentences import split_sentences


def texts(canon: str) -> list[str]:
    return [s.text for s in split_sentences(canon)]


def test_offsets_slice_back():
    canon = normalize(["今天天气很好，他决定出去走走。街上的人不多，风吹过来有些凉。"])
    for s in split_sentences(canon):
        assert canon[s.start : s.end] == s.text


def test_terminators_split():
    canon = "他站起来走到窗户旁边。她问他为什么不早点说？没有一个人愿意回答这个问题！"
    assert texts(canon) == [
        "他站起来走到窗户旁边。",
        "她问他为什么不早点说？",
        "没有一个人愿意回答这个问题！",
    ]


def test_comma_and_semicolon_do_not_split():
    canon = "他来了，她走了；两个人一句话也没有说。"
    assert texts(canon) == [canon]


def test_closing_quote_sticks_to_previous():
    """Кавычка принадлежит реплике, а не тому, что идёт следом."""
    canon = "他想了很久才说：“这件事我不同意。”她点了点头，什么也没有说。"
    assert texts(canon) == ["他想了很久才说：“这件事我不同意。”", "她点了点头，什么也没有说。"]


def test_ellipsis_is_a_terminator():
    canon = "他犹豫了很久也没有下定决心……最后还是走了进去。"
    assert texts(canon) == ["他犹豫了很久也没有下定决心……", "最后还是走了进去。"]


def test_multiple_terminators_are_one_boundary():
    canon = "这真的是你说的话吗？！我完全不敢相信这件事情。"
    assert texts(canon) == ["这真的是你说的话吗？！", "我完全不敢相信这件事情。"]


def test_tail_without_terminator():
    canon = "他站起来走到窗户旁边。她还站在原地没有动"
    assert texts(canon) == ["他站起来走到窗户旁边。", "她还站在原地没有动"]


def test_short_fragment_merges_forward():
    """Огрызок короче 8 символов не должен уезжать в перевод сам по себе."""
    canon = "好。他站起来，慢慢地走到窗户旁边。"
    assert texts(canon) == [canon]


def test_trailing_short_fragment_merges_backward():
    canon = "他站起来，慢慢地走到窗户旁边。好。"
    assert texts(canon) == [canon]


def test_short_paragraph_stays_alone():
    """Абзац — это чаще всего реплика. Склеивать две реплики нельзя."""
    canon = normalize(["“好。”", "“不行。”"])
    assert texts(canon) == ["“好。”", "“不行。”"]


def test_no_merge_across_paragraphs():
    canon = normalize(["他走了。", "她留下了，看着窗外发呆。"])
    assert texts(canon) == ["他走了。", "她留下了，看着窗外发呆。"]


def test_idx_is_sequential():
    canon = normalize(
        ["他站起来走到窗户旁边。她还站在原地没有动。", "第二个段落也有自己的内容在这里。"]
    )
    spans = split_sentences(canon)
    assert [s.idx for s in spans] == [0, 1, 2]
    assert all(canon[s.start : s.end] == s.text for s in spans)


def test_empty_canon():
    assert split_sentences("") == []


# --- английский ---
#
# Все проверки ниже — про одно: в английском точка перегружена, и «резать по
# терминатору» здесь означает резать не там. Каждый тест — отдельный вид
# ложной границы, встречающийся в живом тексте.


def en(canon: str) -> list[str]:
    return [s.text for s in split_sentences(canon, Language.EN)]


def test_en_offsets_slice_back():
    canon = normalize(["The lamp went out. Nobody moved for a long moment."])
    for s in split_sentences(canon, Language.EN):
        assert canon[s.start : s.end] == s.text


def test_en_period_splits():
    canon = "The lamp went out. Nobody moved for a long moment."
    assert en(canon) == ["The lamp went out.", "Nobody moved for a long moment."]


def test_en_leading_space_belongs_to_the_boundary():
    """Пробел после точки не должен уезжать ни в перевод, ни в контекст."""
    assert all(not text.startswith(" ") for text in en("He waited. She did not."))


@pytest.mark.parametrize(
    "canon",
    [
        "Mr. Harrow locked the door and said nothing at all.",
        "They arrived at 3.15 in the morning and waited there.",
        "It was cold, e.g. colder than the night before that one.",
        "J. R. R. signed the letter himself and sealed it twice.",
    ],
)
def test_en_false_boundaries_do_not_split(canon):
    """Сокращение, инициал и дробное число точкой предложение не кончают."""
    assert en(canon) == [canon]


def test_en_quoted_exclamation_keeps_the_attribution():
    """`"Go!" he shouted.` — знак внутри реплики, а не конец фразы.

    Опознаётся по строчной букве после кавычки: заглавная была бы началом
    нового предложения, строчная — продолжением этого.
    """
    canon = '"Go now!" he shouted at the closing door.'
    assert en(canon) == [canon]


def test_en_quoted_sentence_before_a_new_one_still_splits():
    """Заглавная буква после кавычки — это уже новое предложение.

    Реплика здесь намеренно длиннее трёх слов: короткая склеилась бы со
    следующей фразой по правилу огрызков, и проверять было бы нечего.
    """
    canon = '"Leave the door open!" The wind had already reached the stairs.'
    assert en(canon) == [
        '"Leave the door open!"',
        "The wind had already reached the stairs.",
    ]


def test_en_short_fragment_merges_forward():
    """`Yes.` в переводе само по себе бессмысленно — ему нужен сосед."""
    canon = "Yes. The lantern had been burning since the evening."
    assert en(canon) == [canon]


def test_en_two_word_sentence_is_not_a_fragment():
    """Порог в три слова: `He smiled.` — законное предложение, а не огрызок."""
    canon = normalize(["He smiled."])
    assert en(canon) == ["He smiled."]


def test_en_short_paragraph_stays_alone():
    canon = normalize(["Yes.", "No."])
    assert en(canon) == ["Yes.", "No."]


def test_en_ellipsis_terminates():
    canon = "She never finished the sentence… The room stayed quiet after that."
    assert en(canon) == [
        "She never finished the sentence…",
        "The room stayed quiet after that.",
    ]


def test_en_tail_without_terminator_is_a_sentence():
    canon = "The door opened. Someone was standing there"
    assert en(canon) == ["The door opened.", "Someone was standing there"]


def test_language_choice_changes_the_result():
    """Одна и та же строка режется по-разному — правила языковые, не общие."""
    canon = "The lamp went out. Nobody moved at all."
    assert len(en(canon)) == 2
    assert len(split_sentences(canon, Language.ZH)) == 1

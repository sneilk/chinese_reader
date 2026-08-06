"""Резка на предложения.

Главный инвариант: офсеты обязаны резать канон обратно в тот же текст. Если
он нарушен, разъедутся и карточки, и переводы, и подсветка — причём молча.

В тестах на терминаторы фразы намеренно длиннее восьми символов: иначе
срабатывает склейка коротких кусков и проверять становится нечего. Склейка
проверяется отдельно, короткими.
"""

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

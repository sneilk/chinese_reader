"""Сегментация: классификация токенов, userdict и золотой набор.

Главный инвариант тот же, что у резки на предложения: офсеты обязаны резать
канон обратно. Разъедутся они — молча испортятся и карточки, и правка границ.

Золотой набор (segmentation.md §3) лежит рядом в `data/`: 25 коротких
предложений из живой главы, размеченных через `|`. Он не «проверяет
правильность» разметки — он ловит её изменение при донастройке. Пересобирается
осознанно через `scripts/make_golden.py`.
"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import DictEntry
from app.lang.segment import Segmenter, TokenKind, build_userdict, classify, tokens_to_json

DATA = Path(__file__).parent / "data"
GOLDEN = DATA / "segment-golden.txt"
GOLDEN_USERDICT = DATA / "segment-userdict.txt"


@pytest.fixture(scope="module")
def segmenter() -> Segmenter:
    """Один на модуль: загрузка словаря jieba занимает около секунды."""
    return Segmenter(GOLDEN_USERDICT)


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.mark.parametrize(
    ("surface", "kind"),
    [
        ("学习", TokenKind.WORD),
        ("好", TokenKind.WORD),
        ("，", TokenKind.PUNCT),
        ("……", TokenKind.PUNCT),
        (" ", TokenKind.SPACE),
        ("\n", TokenKind.SPACE),
        ("1998", TokenKind.DIGIT),
        ("２０", TokenKind.DIGIT),  # полноширинные цифры — тоже цифры
        ("OK", TokenKind.LATIN),
        ("mp3", TokenKind.LATIN),  # латиница с цифрой уходит в английский словарь
    ],
)
def test_classify(surface, kind):
    assert classify(surface) is kind


@pytest.mark.parametrize("surface", ["11区", "卡拉OK", "x光"])
def test_mixed_tokens_are_words(surface):
    """Смешанные заголовки — полноценные статьи CC-CEDICT, кликабельность им нужна."""
    assert classify(surface) is TokenKind.WORD


def test_offsets_slice_back(segmenter):
    text = "他站起来走到窗户旁边，看见外面下着大雨。"
    for token in segmenter.segment(text):
        assert text[token.start : token.end] == token.surface


def test_tokens_cover_text_without_gaps(segmenter):
    """Токены идут встык: пропуск означал бы непокрытый символ, который не кликнуть."""
    text = "他说：“好。”她点了点头。"
    tokens = segmenter.segment(text)
    assert tokens[0].start == 0
    assert tokens[-1].end == len(text)
    for prev, cur in zip(tokens, tokens[1:], strict=False):
        assert cur.start == prev.end


def test_lookup_key_lowercases_latin_only(segmenter):
    tokens = {t.surface: t for t in segmenter.segment("他喜欢OK的感觉")}
    assert tokens["OK"].lookup_key == "ok"
    assert tokens["喜欢"].lookup_key == "喜欢"


# Имя героини из главы-фикстуры: jieba режет его на фамилию 张 и слово 仙姑.
# Трёхсимвольные имена HMM нередко склеивает и сам, поэтому пример для теста
# нужен именно такой — где без правки границ разметка заведомо неверна.
_SPLIT_NAME = "张仙姑"
_NAME_SENTENCE = f"{_SPLIT_NAME}走了进来。"


def test_add_word_merges_name():
    """Правка границ должна учить сегментатор: §5 segmentation.md."""
    before = Segmenter()
    assert _SPLIT_NAME not in [t.surface for t in before.segment(_NAME_SENTENCE)]

    after = Segmenter()
    after.add_word(_SPLIT_NAME)
    assert _SPLIT_NAME in [t.surface for t in after.segment(_NAME_SENTENCE)]


def test_add_word_does_not_leak_between_instances():
    """Слово одного пользователя не должно менять разметку в другом экземпляре."""
    trained = Segmenter()
    trained.add_word(_SPLIT_NAME)
    fresh = Segmenter()
    assert _SPLIT_NAME not in [t.surface for t in fresh.segment(_NAME_SENTENCE)]


def test_build_userdict_skips_words_jieba_knows(session, tmp_path):
    """Своя частота у jieba точнее нашей базовой тройки — перебивать её нельзя."""
    session.add_all(
        [
            DictEntry(headword="中国", senses_json="[]", source="cedict"),  # jieba знает
            DictEntry(headword="江雪明", senses_json="[]", source="cedict"),  # не знает
        ]
    )
    session.commit()

    path = tmp_path / "userdict.txt"
    assert build_userdict(session, path) == 1
    assert path.read_text(encoding="utf-8").split() == ["江雪明", "3"]


def test_build_userdict_length_and_script_filter(session, tmp_path):
    session.add_all(
        [
            DictEntry(headword="兲", senses_json="[]", source="cedict"),  # один символ
            DictEntry(headword="一二三四五六", senses_json="[]", source="cedict"),  # длинное
            DictEntry(headword="OK", senses_json="[]", source="cedict"),  # не иероглифы
            DictEntry(headword="11区", senses_json="[]", source="cedict"),  # смешанное
        ]
    )
    session.commit()

    path = tmp_path / "userdict.txt"
    assert build_userdict(session, path) == 0


def test_tokens_to_json(segmenter):
    text = "他走了。"
    dumped = tokens_to_json(segmenter.segment(text))
    assert dumped.startswith("[[0,")
    assert '"word"' in dumped and '"punct"' in dumped


def test_golden_set(segmenter):
    """Регресс-снапшот: разметка изменилась — значит изменилась осознанно."""
    lines = GOLDEN.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 20, "золотой набор — 20–30 предложений"

    for line in lines:
        expected = line.split("|")
        text = "".join(expected)
        assert [t.surface for t in segmenter.segment(text)] == expected, text


def test_golden_offsets_slice_back(segmenter):
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        text = line.replace("|", "")
        tokens = segmenter.segment(text)
        assert all(text[t.start : t.end] == t.surface for t in tokens)
        assert tokens[-1].end == len(text)

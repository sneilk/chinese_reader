"""Резка канона на предложения.

Предложение — единица перевода (translation.md §6), поэтому правила
зафиксированы, а не подбираются по ходу. Правила из RFC §5.2:

* терминаторы — 。！？!? и многоточие, плюс граница абзаца;
* закрывающие кавычки и скобки прилипают к предыдущему предложению;
* ；и ，не режут;
* кусок короче 8 символов склеивается со следующим.

Последнее правило задевает каждое шестое предложение (25 из 153 на живой
главе, T0.5), поэтому оно не косметика.

Уточнение к RFC: склейка коротких кусков идёт **только внутри абзаца**.
Абзац в китайской новелле — это чаще всего реплика, и склеить короткую реплику
со следующей значило бы отдать переводчику диалог двух персонажей как одну
фразу. Короткий абзац остаётся отдельным предложением: это законная единица
перевода, а не огрызок.

## Английский: та же схема, другие правила

Каркас — абзац, терминаторы, склейка коротких — общий, меняются только два
правила, и оба потому, что в английском точка перегружена.

**Точка не всегда конец предложения.** `Mr.`, `e.g.`, `J. R. R.`, `3.5` — во
всех случаях за точкой продолжается та же фраза. Поэтому граница признаётся,
только когда за терминатором идёт пробел, а за ним — прописная буква или
открывающая кавычка. Это же правило бесплатно решает главную беду английских
диалогов: в `"Go!" he shouted.` восклицательный знак стоит **внутри** фразы,
и по одному лишь знаку её разрезало бы пополам — а по строчной `he` видно,
что предложение продолжается.

**Короткий кусок меряется словами, а не символами.** Восемь символов — это
четыре-пять иероглифов, то есть половина мысли, но полноценное английское
`He smiled.` Порог в три слова оставляет такие фразы в покое и склеивает
только настоящие огрызки вроде `Yes.`
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from app.domain import Language

# Многоточие в китайском наборе — это …… (две штуки), но встречается и одна.
_ZH_TERMINATORS = "。！？!?…"
# Прилипают к предыдущему предложению, иначе закрывающая кавычка открыла бы
# следующее: 他说：“好。” — кавычка принадлежит реплике, а не тому, что дальше.
_ZH_CLOSERS = "」』”’）】》〉、〕｝)\"'"

_MIN_SENTENCE_CHARS = 8
_MIN_SENTENCE_WORDS = 3

_ZH_SPLIT_RE = re.compile(f"[{re.escape(_ZH_TERMINATORS)}]+[{re.escape(_ZH_CLOSERS)}]*")

_EN_SPLIT_RE = re.compile("[.!?…]+[\"'”’)\\]»]*")
# Слово прямо перед точкой — по нему опознаётся сокращение.
_EN_WORD_BEFORE_DOT = re.compile(r"([A-Za-z]+)\.$")
# Чем может начинаться предложение помимо прописной буквы: кавычки, скобка,
# тире прямой речи, цифра.
_EN_OPENERS = "\"'“‘([«—–-"

# Сокращения, после которых точка не кончает предложение. Список закрытый и
# короткий намеренно: каждое лишнее слово здесь — это пропущенная граница
# там, где оно окажется последним словом фразы.
_EN_ABBREVIATIONS = frozenset(
    """
    mr mrs ms mx dr prof rev fr sr jr st gen col sgt lt capt sen gov
    vs etc al cf ca approx dept est fig no nos vol pp ed eds
    inc ltd co corp univ
    jan feb mar apr jun jul aug sep sept oct nov dec
    mon tue tues wed thu thur thurs fri sat sun
    am pm
    """.split()
)


@dataclass(frozen=True)
class SentenceSpan:
    """Границы предложения в каноне. `text` — срез канона, не копия смысла."""

    idx: int
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class Rules:
    """Всё, чем язык отличается при резке: где границы и что считать огрызком."""

    boundaries: Callable[[str], list[int]]
    too_short: Callable[[str], bool]


def _zh_boundaries(par: str) -> list[int]:
    return [m.end() for m in _ZH_SPLIT_RE.finditer(par)]


def _en_is_boundary(par: str, match: re.Match[str]) -> bool:
    """Настоящая ли это граница предложения. Разбор случаев — в шапке модуля."""
    end = match.end()
    if end >= len(par):
        return True
    if not par[end].isspace():
        # `3.5`, `e.g.something` — точка внутри слова или числа.
        return False

    rest = par[end:].lstrip()
    if not rest:
        return True
    nxt = rest[0]
    if not (nxt.isupper() or nxt.isdigit() or nxt in _EN_OPENERS):
        # Строчная буква после знака — это авторская речь после реплики:
        # «"Go!" he shouted.» Резать здесь нельзя.
        return False

    if match.group() == ".":
        word = _EN_WORD_BEFORE_DOT.search(par[:end])
        if word is not None:
            head = word.group(1)
            # Инициал (`J. R. R.`) — тот же случай, что и сокращение: за
            # точкой идёт прописная буква, и без этой проверки имя героя
            # рассыпалось бы на три предложения.
            if head.lower() in _EN_ABBREVIATIONS or (len(head) == 1 and head.isupper()):
                return False
    return True


def _en_boundaries(par: str) -> list[int]:
    return [m.end() for m in _EN_SPLIT_RE.finditer(par) if _en_is_boundary(par, m)]


RULES: dict[Language, Rules] = {
    Language.ZH: Rules(
        boundaries=_zh_boundaries,
        too_short=lambda text: len(text.strip()) < _MIN_SENTENCE_CHARS,
    ),
    Language.EN: Rules(
        boundaries=_en_boundaries,
        too_short=lambda text: len(text.split()) < _MIN_SENTENCE_WORDS,
    ),
}


def _split_paragraph(par: str, offset: int, rules: Rules) -> list[tuple[int, int]]:
    """Разрезать один абзац, вернуть пары офсетов в координатах канона."""
    spans: list[tuple[int, int]] = []
    pos = 0
    for end in rules.boundaries(par):
        if end > pos:
            spans.append((offset + pos, offset + end))
            pos = end
    # Хвост без терминатора — тоже предложение.
    if pos < len(par):
        spans.append((offset + pos, offset + len(par)))
    return spans


def _merge_short(spans: list[tuple[int, int]], text: str, rules: Rules) -> list[tuple[int, int]]:
    """Склеить куски короче порога со следующим — в пределах одного абзаца."""
    if len(spans) < 2:
        return spans

    merged: list[tuple[int, int]] = []
    pending: tuple[int, int] | None = None
    for start, end in spans:
        if pending is not None:
            start = pending[0]
            pending = None
        if rules.too_short(text[start:end]):
            pending = (start, end)
            continue
        merged.append((start, end))

    if pending is not None:
        # Последний кусок склеивать не с чем впереди — прицепим к предыдущему.
        if merged:
            merged[-1] = (merged[-1][0], pending[1])
        else:
            merged.append(pending)
    return merged


def split_sentences(canon: str, lang: Language | str = Language.ZH) -> list[SentenceSpan]:
    """Разрезать канон главы. Офсеты пригодны для среза: canon[start:end]."""
    rules = RULES[Language(lang)]
    result: list[SentenceSpan] = []
    offset = 0
    for par in canon.split("\n"):
        if par.strip():
            spans = _merge_short(_split_paragraph(par, offset, rules), canon, rules)
            for start, end in spans:
                # Пробел после точки принадлежит границе, а не следующей фразе.
                # В китайском его там не бывает, в английском — всегда, и без
                # этого он уезжал бы и в запрос к переводчику, и в контекст
                # сохранённого слова.
                while start < end and canon[start].isspace():
                    start += 1
                if start >= end:
                    continue
                result.append(
                    SentenceSpan(idx=len(result), start=start, end=end, text=canon[start:end])
                )
        # +1 — сам перевод строки, он в предложения не входит.
        offset += len(par) + 1
    return result

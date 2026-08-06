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
"""

import re
from dataclasses import dataclass

# Многоточие в китайском наборе — это …… (две штуки), но встречается и одна.
_TERMINATORS = "。！？!?…"
# Прилипают к предыдущему предложению, иначе закрывающая кавычка открыла бы
# следующее: 他说：“好。” — кавычка принадлежит реплике, а не тому, что дальше.
_CLOSERS = "」』”’）】》〉、〕｝)\"'"

_MIN_SENTENCE_CHARS = 8

_SPLIT_RE = re.compile(f"[{re.escape(_TERMINATORS)}]+[{re.escape(_CLOSERS)}]*")


@dataclass(frozen=True)
class SentenceSpan:
    """Границы предложения в каноне. `text` — срез канона, не копия смысла."""

    idx: int
    start: int
    end: int
    text: str


def _split_paragraph(par: str, offset: int) -> list[tuple[int, int]]:
    """Разрезать один абзац, вернуть пары офсетов в координатах канона."""
    spans: list[tuple[int, int]] = []
    pos = 0
    for m in _SPLIT_RE.finditer(par):
        end = m.end()
        if end > pos:
            spans.append((offset + pos, offset + end))
            pos = end
    # Хвост без терминатора — тоже предложение.
    if pos < len(par):
        spans.append((offset + pos, offset + len(par)))
    return spans


def _merge_short(spans: list[tuple[int, int]], text: str) -> list[tuple[int, int]]:
    """Склеить куски короче порога со следующим — в пределах одного абзаца."""
    if len(spans) < 2:
        return spans

    merged: list[tuple[int, int]] = []
    pending: tuple[int, int] | None = None
    for start, end in spans:
        if pending is not None:
            start = pending[0]
            pending = None
        if len(text[start:end].strip()) < _MIN_SENTENCE_CHARS:
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


def split_sentences(canon: str) -> list[SentenceSpan]:
    """Разрезать канон главы. Офсеты пригодны для среза: canon[start:end]."""
    result: list[SentenceSpan] = []
    offset = 0
    for par in canon.split("\n"):
        if par.strip():
            spans = _merge_short(_split_paragraph(par, offset), canon)
            for start, end in spans:
                result.append(
                    SentenceSpan(idx=len(result), start=start, end=end, text=canon[start:end])
                )
        # +1 — сам перевод строки, он в предложения не входит.
        offset += len(par) + 1
    return result

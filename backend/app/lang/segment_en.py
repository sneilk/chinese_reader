"""Разрезка английского текста на токены.

Китайскому нужен jieba, потому что границы слов в тексте не обозначены вовсе
(segmentation.md §1). Английскому словарь не нужен: границы уже проставлены
пробелами, и вся работа — аккуратно их прочитать. Отсюда регулярка вместо
модели и ноль состояния: токенизатору здесь нечего загружать и нечему
учиться, поэтому он функция, а не объект.

Три требования, из которых выведено всё остальное.

**Покрытие сплошное.** Фронт собирает главу обратно из токенов
(`ChapterText.tsx`), а не из исходного текста. Пропущенный пробел — это
пропавший пробел на экране, поэтому промежутки между словами тоже становятся
токенами, а не выбрасываются.

**Перевод строки — отдельный токен.** По нему фронт режет главу на абзацы,
ровно как в китайском варианте. Склеенный с соседней пунктуацией, он оставил
бы главу одним абзацем на три тысячи слов.

**Апостроф остаётся внутри слова.** `don't` — одно слово, и разрезать его на
`don`, `'`, `t` значит выдать читателю два бессмысленных куска. Со словарём
такое слово разбирается отдельно (`lang/lemma.py`).
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from app.lang.segment import Token, TokenKind, classify

# Слово: буквы плюс внутренние апострофы (прямой и типографский). Дефис в
# слово не входит намеренно: `well-known` в словаре искать негде, а `well` и
# `known` по отдельности находятся оба.
_WORD_RE = r"[A-Za-z]+(?:['’][A-Za-z]+)*"
# Число: с разделителями групп и дробной частью — `1,000`, `3.15`.
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")

_TOKEN_RE = re.compile(f"{_WORD_RE}|{_NUMBER_RE.pattern}")


def _gap_pieces(gap: str, offset: int) -> Iterator[tuple[int, int, str]]:
    """Разложить промежуток между словами, выделив переводы строк отдельно."""
    pos = 0
    for i, char in enumerate(gap):
        if char != "\n":
            continue
        if i > pos:
            yield offset + pos, offset + i, gap[pos:i]
        yield offset + i, offset + i + 1, "\n"
        pos = i + 1
    if pos < len(gap):
        yield offset + pos, offset + len(gap), gap[pos:]


def _spans(text: str) -> Iterator[tuple[int, int, str]]:
    pos = 0
    for m in _TOKEN_RE.finditer(text):
        if m.start() > pos:
            yield from _gap_pieces(text[pos : m.start()], pos)
        yield m.start(), m.end(), m.group()
        pos = m.end()
    if pos < len(text):
        yield from _gap_pieces(text[pos:], pos)


def segment(text: str) -> list[Token]:
    """Разрезать английский текст. Офсеты пригодны для среза: `text[start:end]`."""
    tokens: list[Token] = []
    for start, end, surface in _spans(text):
        kind = classify(surface)
        if kind is TokenKind.PUNCT and _NUMBER_RE.fullmatch(surface):
            # `3.15` и `1,000` общая классификация относит к пунктуации: цифр
            # там больше, чем знаков, но `^\d+$` они не удовлетворяют. Здесь
            # мы знаем, что это число, — и от рода зависит, кликабельно ли оно.
            kind = TokenKind.DIGIT
        key = surface.lower() if kind is TokenKind.LATIN else surface
        tokens.append(Token(start, end, surface, kind, key))
    return tokens

"""Пиньинь с цифрами тона → пиньинь с диакритикой.

CC-CEDICT хранит чтение как `chuan2 tong3`, а читателю нужно `chuán tǒng`
(решение B4: пиньинь в карточке всегда). Преобразование чисто механическое,
поэтому живёт отдельной функцией без словаря и без зависимостей.

Правило выбора буквы под знак тона стандартное: приоритет у `a`, затем `o`,
затем `e`; в сочетаниях `iu` и `ui` знак ставится на второй гласной; иначе —
на единственной или последней гласной слога.
"""

import re

_TONES = {
    "a": "āáǎàa",
    "e": "ēéěèe",
    "i": "īíǐìi",
    "o": "ōóǒòo",
    "u": "ūúǔùu",
    "ü": "ǖǘǚǜü",
}

_SYLLABLE = re.compile(r"([A-Za-zÜü:]+)([1-5])?")


def _mark(syllable: str, tone: int) -> str:
    # CC-CEDICT записывает ü как u: или как v.
    syllable = syllable.replace("u:", "ü").replace("U:", "Ü").replace("v", "ü")
    if tone == 5:
        return syllable

    lowered = syllable.lower()
    idx = -1
    if "a" in lowered:
        idx = lowered.index("a")
    elif "o" in lowered:
        idx = lowered.index("o")
    elif "e" in lowered:
        idx = lowered.index("e")
    elif "iu" in lowered:
        idx = lowered.index("iu") + 1
    elif "ui" in lowered:
        idx = lowered.index("ui") + 1
    else:
        for i, ch in enumerate(lowered):
            if ch in _TONES:
                idx = i
    if idx < 0:
        return syllable

    letter = lowered[idx]
    marked = _TONES[letter][tone - 1]
    if syllable[idx].isupper():
        marked = marked.upper()
    return syllable[:idx] + marked + syllable[idx + 1 :]


def numbered_to_accented(reading: str) -> str:
    """`chuan2 tong3` → `chuán tǒng`. Неразобранное отдаётся как есть."""
    if not reading:
        return ""

    out = []
    for part in reading.split():
        m = _SYLLABLE.fullmatch(part)
        if not m:
            # Пунктуация и служебные пометки вроде · или xx — не трогаем.
            out.append(part.replace("u:", "ü"))
            continue
        letters, tone = m.group(1), m.group(2)
        out.append(_mark(letters, int(tone)) if tone else letters.replace("u:", "ü"))
    return " ".join(out)

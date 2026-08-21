"""Поиск слова в локальных словарях.

Порядок источников — БКРС, затем CC-CEDICT (RFC §8): русское значение читателю
полезнее английского, а английское остаётся запасным. БКРС появится в T2.2, но
порядок задан здесь и сейчас, чтобы его подключение было импортом данных, а не
правкой логики.

**Фолбэк по символам — не украшение, а основной случай.** Имена героев веб-новелл
в словарях отсутствуют по определению: `张仙姑` не найдётся никогда. Разложить
такое слово на знаки и показать чтение и значение каждого — единственный способ
дать осмысленную карточку вместо «не найдено» (RFC §5.5). Помечается это
явно, чтобы читатель не принял сумму значений знаков за перевод слова.

## Английский: вместо разбора по знакам — перебор форм

У английского обратная беда. Слово в тексте почти всегда стоит не в той форме,
в какой лежит в словаре: `running`, `wolves`, `didn't`. Разбирать его по
буквам бессмысленно — буква ничего не значит, — зато можно перебрать формы,
под которыми оно может быть записано (`lang/lemma.py`), и взять первое
попадание. Найденная форма возвращается наружу: читатель должен видеть, что
карточка про `run`, когда в тексте стоит `running`.

Порядок кандидатов задан там же, и первым всегда идёт то, что написано в
тексте: `saw` — это и пила, и прошедшее от `see`, и подменять одно другим,
не спросив словарь, мы не вправе.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DictEntry
from app.domain import Language
from app.lang.lemma import candidates

# Порядок источников на выдаче: чем меньше число, тем выше статья.
# `endict` — англо-русский словарь; для английских слов он единственный, а в
# китайской выдаче не появляется вовсе: у статей другой `lang`.
SOURCE_ORDER = {"bkrs": 0, "endict": 0, "cedict": 1}
_UNKNOWN_SOURCE = 99

_HAN_RE = re.compile(r"^[一-鿿]+$")

# Длиннее этого заголовок словарной статьи для карточки бесполезен: там
# начинаются пословицы и целые фразы, которых читатель не выделял.
MAX_HEADWORD_CHARS = 4


@dataclass(frozen=True)
class Entry:
    headword: str
    reading: str | None
    senses: list[str]
    source: str
    traditional: str | None = None


@dataclass(frozen=True)
class CharGloss:
    """Значение одного знака — для слова, которого нет в словаре."""

    char: str
    reading: str | None
    senses: list[str]


@dataclass(frozen=True)
class LookupResult:
    word: str
    entries: list[Entry] = field(default_factory=list)
    chars: list[CharGloss] = field(default_factory=list)
    #: Форма, под которой слово нашлось, если она отличается от исходной:
    #: `running` найдено как `run`. `None` — совпало как есть.
    matched: str | None = None

    @property
    def found(self) -> bool:
        return bool(self.entries)

    @property
    def approximate(self) -> bool:
        """Карточка собрана из знаков, а не из статьи о слове целиком."""
        return not self.entries and bool(self.chars)


def _to_entry(row: DictEntry) -> Entry:
    try:
        senses = json.loads(row.senses_json)
    except (TypeError, ValueError):
        senses = []
    return Entry(
        headword=row.headword,
        reading=row.reading,
        senses=[str(s) for s in senses],
        source=row.source,
        traditional=row.traditional,
    )


def _entries_for(session: Session, word: str, lang: str) -> list[Entry]:
    rows = session.scalars(
        select(DictEntry).where(DictEntry.lang == lang, DictEntry.headword == word)
    ).all()
    ordered = sorted(rows, key=lambda r: SOURCE_ORDER.get(r.source, _UNKNOWN_SOURCE))
    entries = [_to_entry(row) for row in ordered]

    # Статья без значений показывать нечего, но она объявляет слово найденным:
    # встанет первой по порядку источников, отключит фолбэк по знакам — и
    # читатель получит пустую карточку вместо разбора имени героя.
    return [e for e in entries if e.senses]


def lookup(session: Session, word: str, lang: str = Language.ZH) -> LookupResult:
    """Найти слово. Пустой результат означает, что показывать нечего вообще."""
    word = word.strip()
    if not word:
        return LookupResult(word=word)

    if Language(lang) is Language.EN:
        return _lookup_en(session, word)

    entries = _entries_for(session, word, lang)
    if entries:
        return LookupResult(word=word, entries=entries)

    # Статьи нет. Для иероглифического слова собираем карточку из знаков;
    # для латиницы и цифр разбирать нечего.
    if not _HAN_RE.match(word):
        return LookupResult(word=word)

    chars: list[CharGloss] = []
    for char in word:
        found = _entries_for(session, char, lang)
        first = found[0] if found else None
        chars.append(
            CharGloss(
                char=char,
                reading=first.reading if first else None,
                senses=first.senses[:3] if first else [],
            )
        )
    return LookupResult(word=word, chars=chars)


def _lookup_en(session: Session, word: str) -> LookupResult:
    """Перебрать формы слова до первого попадания в англо-русский словарь."""
    for form in candidates(word):
        entries = _entries_for(session, form, Language.EN)
        if entries:
            return LookupResult(
                word=word,
                entries=entries,
                matched=form if form != word else None,
            )
    return LookupResult(word=word)


def is_dictionary_headword(word: str) -> bool:
    """Годится ли слово как заголовок статьи: иероглифы и не длиннее четырёх."""
    return bool(_HAN_RE.match(word)) and len(word) <= MAX_HEADWORD_CHARS

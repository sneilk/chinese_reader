"""Сегментация китайского текста.

Реализация по [segmentation.md](../../../docs/segmentation.md): jieba, словарь
приложения в userdict, токены с офсетами. Токен хранит границы, а не строку —
тогда оригинальный текст остаётся нетронутым, контекст для карточки режется
по офсетам, а правка границ становится правкой разметки, а не текста.

Ключевая мысль §2 того документа: сегментатор и словарь обязаны говорить об
одном и том же. Иначе jieba склеит то, чего в словаре нет (клик даёт «не
найдено»), либо разрежет то, что есть единой статьёй (пользователь видит два
бессмысленных куска).
"""

from __future__ import annotations

import enum
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DictEntry

log = logging.getLogger(__name__)

_HAN_RE = re.compile(r"^[一-鿿]+$")
_HAS_HAN_RE = re.compile(r"[一-鿿]")
_HAS_LATIN_RE = re.compile(r"[A-Za-zＡ-Ｚａ-ｚ]")
_DIGIT_RE = re.compile(r"^\d+$")

# Длина заголовка, попадающего в userdict. Односимвольные jieba и так знает,
# а длинные редкие статьи начинают выигрывать у коротких употребимых.
MIN_USERDICT_LEN = 2
MAX_USERDICT_LEN = 4

# Частота для слов из словаря. Не украшение: jieba выбирает разбиение по
# максимуму произведения частот, и без явного значения редкие длинные слова
# перетягивают на себя. Значение низкое намеренно — статья CC-CEDICT говорит
# «такое слово бывает», а не «оно частое».
CEDICT_FREQ = 3


class TokenKind(enum.StrEnum):
    WORD = "word"
    PUNCT = "punct"
    LATIN = "latin"
    DIGIT = "digit"
    SPACE = "space"


@dataclass(frozen=True)
class Token:
    start: int
    end: int
    surface: str
    kind: TokenKind
    lookup_key: str


def classify(surface: str) -> TokenKind:
    """Определить род токена. От него зависит, кликабелен ли он на фронте.

    Смешанные токены решаются по наличию иероглифа, а не по «чистоте» строки:
    `11区` и `卡拉OK` — полноценные статьи CC-CEDICT, и уехать в `punct`
    (то есть перестать быть кликабельными) они не должны.
    """
    if not surface.strip():
        return TokenKind.SPACE
    if _HAS_HAN_RE.search(surface):
        return TokenKind.WORD
    if _HAS_LATIN_RE.search(surface):
        return TokenKind.LATIN
    if _DIGIT_RE.match(surface):
        return TokenKind.DIGIT
    return TokenKind.PUNCT


def build_userdict(session: Session, path: Path) -> int:
    """Выгрузить заголовки словаря в файл userdict для jieba.

    В файл идут только слова, которых у jieba нет. Причина — `load_userdict`
    перезаписывает частоту без оглядки на прежнюю, а свои частоты jieba знает
    лучше нас: у 中国 их 129470, и подмена на базовую тройку рассыпала бы
    употребимые слова на символы. Замерено на живой главе: словарь целиком
    режет 一点 на 一 и 点, а 星月光亮 — на 星月光 и 亮.

    Ровно это и советует segmentation.md §2: частота из штатного словаря там,
    где слово известно; низкая базовая — только для остальных.
    """
    import jieba

    jieba.dt.initialize()
    known = jieba.dt.FREQ

    path.parent.mkdir(parents=True, exist_ok=True)
    rows = session.execute(select(DictEntry.headword).distinct()).scalars()

    written = 0
    seen: set[str] = set()
    with path.open("w", encoding="utf-8") as fh:
        for hw in rows:
            if hw in seen or not (MIN_USERDICT_LEN <= len(hw) <= MAX_USERDICT_LEN):
                continue
            if not _HAN_RE.match(hw) or known.get(hw):
                continue
            seen.add(hw)
            fh.write(f"{hw} {CEDICT_FREQ}\n")
            written += 1
    log.info("userdict: %s слов -> %s", written, path)
    return written


class Segmenter:
    """Обёртка над jieba. Один экземпляр на приложение: загрузка словаря дорогая.

    Внутри — собственный `jieba.Tokenizer`, а не модульные функции. Модульные
    работают с единственным глобальным словарём процесса, и тогда `add_word`
    для слова пользователя течёт во все остальные тексты и во все тесты
    разом — отладка такого стоит дороже, чем лишний экземпляр.
    """

    def __init__(self, userdict: Path | None = None) -> None:
        import jieba

        self._dt = jieba.Tokenizer()
        if userdict is not None and userdict.exists():
            self._dt.load_userdict(str(userdict))
            log.info("userdict загружен: %s", userdict)

    def add_word(self, word: str, freq: int = 10_000) -> None:
        """Слово пользователя. Частота заведомо высокая: он выделил его явно."""
        self._dt.add_word(word, freq=freq)

    def segment(self, text: str) -> list[Token]:
        tokens: list[Token] = []
        for surface, start, end in self._dt.tokenize(text):
            kind = classify(surface)
            key = surface.lower() if kind is TokenKind.LATIN else surface
            tokens.append(Token(start, end, surface, kind, key))
        return tokens


def tokens_to_json(tokens: list[Token]) -> str:
    """Компактная форма для хранения и отдачи фронту: [[start, end, kind], ...]."""
    return json.dumps([[t.start, t.end, t.kind.value] for t in tokens], ensure_ascii=False)

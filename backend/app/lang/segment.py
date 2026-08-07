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
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DictEntry, UserWord

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

# Частота для слов читателя. Заведомо высокая: он поправил границы руками,
# значит хочет видеть это слово целиком, а не бороться со статистикой.
USER_FREQ = 10_000
# Имена героев бывают длиннее словарных заголовков, поэтому свой потолок.
MAX_USER_WORD_LEN = 8

# Источники, годные для резки. CC-CEDICT — словарь употребимых слов, и его
# заголовки согласуют сегментатор со словарём. БКРС сюда не входит: он полон
# редких сочетаний, и в userdict превращает текст в труху (см. build_userdict).
SEGMENTATION_SOURCES: tuple[str, ...] = ("cedict",)


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


def build_userdict(
    session: Session,
    path: Path,
    sources: Sequence[str] = SEGMENTATION_SOURCES,
) -> int:
    """Выгрузить заголовки словаря в файл userdict для jieba.

    В файл идут только слова, которых у jieba нет. Причина — `load_userdict`
    перезаписывает частоту без оглядки на прежнюю, а свои частоты jieba знает
    лучше нас: у 中国 их 129470, и подмена на базовую тройку рассыпала бы
    употребимые слова на символы. Замерено на живой главе: словарь целиком
    режет 一点 на 一 и 点, а 星月光亮 — на 星月光 и 亮.

    Ровно это и советует segmentation.md §2: частота из штатного словаря там,
    где слово известно; низкая базовая — только для остальных.

    Слова читателя — исключение из этого правила: они идут в файл всегда и с
    заведомо высокой частотой, даже если jieba такое слово знает. Он выделил
    его руками, поправив границы (§5), и его решение важнее статистики.

    **В сегментацию идут не все источники.** Большой словарь (БКРС) содержит
    почти любое двух-четырёхзначное сочетание, и залив его сюда с базовой
    частотой, мы рассыпаем текст: замерено на золотом наборе — 23 предложения
    из 25 меняют разметку, `天黑得像几百年` превращается в `天|黑得像几|百年没擦`.
    Причина в том, что это разные задачи: словарь нужен **карточке**, а
    userdict — **резке**, и согласовывать их (segmentation.md §2) значит брать
    оттуда слова, а не всё подряд.
    """
    import jieba

    jieba.dt.initialize()
    known = jieba.dt.FREQ

    path.parent.mkdir(parents=True, exist_ok=True)
    rows = session.execute(
        select(DictEntry.headword).distinct().where(DictEntry.source.in_(sources))
    ).scalars()

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

        # Слова читателя пишутся последними: при равных заголовках побеждает
        # последняя строка файла, и это должна быть его частота, а не наша.
        for hw in session.execute(select(UserWord.headword).distinct()).scalars():
            if not is_teachable(hw):
                continue
            fh.write(f"{hw} {USER_FREQ}\n")
            written += 1

    log.info("userdict: %s слов -> %s", written, path)
    return written


def is_teachable(headword: str) -> bool:
    """Годится ли слово в userdict.

    Односимвольные не нужны: jieba и так режет по одному знаку, когда не
    находит слова. Латиница и цифры сегментатору китайского тоже ни к чему.
    """
    return bool(_HAN_RE.match(headword)) and MIN_USERDICT_LEN <= len(headword) <= MAX_USER_WORD_LEN


def teach_word(segmenter: Segmenter | None, path: Path | None, headword: str) -> bool:
    """Научить сегментатор слову читателя. Возвращает «слово принято».

    Двумя путями сразу, и оба нужны. Живой экземпляр правится в памяти —
    иначе следующая глава в этом же процессе резалась бы по-старому. Файл
    дописывается — иначе правка не пережила бы перезапуск сервиса.

    Источник правды при этом — таблица `user_words`: файл пересобирается из
    неё целиком (`build_userdict`), а дописывание строки — лишь способ не
    перечитывать двадцать семь тысяч строк ради одного слова.
    """
    headword = headword.strip()
    if not is_teachable(headword):
        return False

    if segmenter is not None:
        segmenter.add_word(headword, freq=USER_FREQ)

    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{headword} {USER_FREQ}\n")

    log.info("сегментатор научен слову %s", headword)
    return True


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

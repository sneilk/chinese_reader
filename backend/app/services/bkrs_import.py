"""Импорт БКРС из формата DSL.

Формат (T0.7): плоский текстовый файл, строка-заголовок без отступа, строки
карточки — с отступом (в дампе это табуляция, но принимаем и пробелы).
Несколько заголовков подряд относятся к одной карточке: так записывают
варианты написания. Отличить их от «заголовка без тела» нельзя в принципе —
запись одна и та же, — поэтому пустой остаётся только оборванная последняя.

    一见如故
        [m1][p]устойчивое выражение[/p][/m]
        [m2]сойтись с первой встречи[/m]
        [m2][ex]他们一见如故 они сошлись сразу[/ex][/m]

Разметка — закрытый набор тегов, и обращаться с ними надо по-разному:

* `[m1]`, `[m2]` — уровни списка, `[c]`, `[i]`, `[b]`, `[u]` — оформление:
  снимаем, содержимое оставляем;
* `[ex]` и `[*]` — примеры: выбрасываем **вместе с содержимым**. В карточке
  они занимают больше места, чем значения, а читателю в MVP не нужны;
* `[p]` — **пометы**: «сущ.», «диал.». Выбрасываем, в списке значений они
  только мешают.

**Чтение лежит не в `[p]`, а отдельной строкой сразу после заголовка** — это
выяснилось на живой фикстуре T0.7, где из двадцати семи статей пиньинь не
нашёлся ни у одной, хотя в файле его восемьдесят девять слогов. Описание в
задаче («`[p]` — пиньинь и пометы») оказалось неточным, и проверить это можно
было только на настоящем дампе.

Экранирование в DSL — обратной косой: `\\[`, `\\]`, `\\\\`. Снимаем его
последним, иначе экранированная скобка будет принята за тег.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
import urllib.request
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import DictEntry

log = logging.getLogger(__name__)

SOURCE = "bkrs"
DAILY_URL = "https://bkrs.info/downloads/daily/dabkrs_{date}.gz"

# Блочные теги вместе с содержимым: примеры в карточке не нужны.
_DROP_BLOCKS = re.compile(r"\[(ex|\*)\].*?\[/\1\]", re.DOTALL)
# Пометы и чтение.
_P_BLOCK = re.compile(r"\[p\](.*?)\[/p\]", re.DOTALL)
# Любой оставшийся тег: снимаем разметку, содержимое оставляем.
_ANY_TAG = re.compile(r"\[/?[^\[\]]{0,32}?\]")
# Ссылка на другую статью: показываем слово, а не разметку.
_REF = re.compile(r"<<(.+?)>>")
_ESCAPED = re.compile(r"\\(.)")
_SPACES = re.compile(r"[ \t]+")

# Подстановки под экранированные символы: в тексте статьи их быть не может,
# потому что это управляющие коды, а не печатные знаки.
_ESC_OPEN = "\x00o\x00"
_ESC_CLOSE = "\x00c\x00"
_ESC_BACKSLASH = "\x00b\x00"

# Пиньинь: латиница с диакритикой или с цифрой тона, пробелы и апострофы.
_PINYIN = re.compile(r"^[a-zA-ZüÜāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜɑ\d\s'·,;.\-]+$")

_HAN = re.compile(r"[一-鿿]")

# Заголовок длиннее — это фраза или пословица, карточке она не нужна.
# Потолок выше словарного (4) намеренно: столько же, сколько у слов читателя.
MAX_HEADWORD_LEN = 8

# Директивы шапки DSL. Проверяем по списку, а не по одной решётке: заголовок
# статьи тоже может начинаться с неё, и тогда он пропал бы молча, а его тело
# приклеилось бы к предыдущей карточке.
_DIRECTIVES = (
    "#NAME",
    "#INDEX_LANGUAGE",
    "#CONTENTS_LANGUAGE",
    "#SOURCE_CODE_PAGE",
    "#ICON_FILE",
    "#INCLUDE",
)


@dataclass
class BkrsEntry:
    headwords: list[str]
    senses: list[str] = field(default_factory=list)
    reading: str | None = None

    @property
    def usable(self) -> bool:
        return bool(self.headwords and self.senses)


def strip_markup(line: str) -> str:
    """Снять разметку DSL, оставив читаемый текст.

    Экранированные скобки прячутся под подстановку до разбора тегов и
    возвращаются после. Без этого `\\[в тексте\\]` целиком считается тегом и
    исчезает вместе с содержимым — а это обычный текст статьи.
    """
    line = line.replace("\\\\", _ESC_BACKSLASH)
    line = line.replace("\\[", _ESC_OPEN).replace("\\]", _ESC_CLOSE)

    line = _DROP_BLOCKS.sub("", line)
    line = _P_BLOCK.sub("", line)
    line = _ANY_TAG.sub("", line)
    line = _REF.sub(r"\1", line)
    line = _ESCAPED.sub(r"\1", line)

    line = line.replace(_ESC_OPEN, "[").replace(_ESC_CLOSE, "]")
    line = line.replace(_ESC_BACKSLASH, "\\")
    return _SPACES.sub(" ", line).strip()


def looks_like_pinyin(text: str) -> bool:
    """Отличить чтение от пометы: «yī jiàn rú gù» против «сущ.»."""
    text = text.strip()
    if not text or _HAN.search(text):
        return False
    return bool(_PINYIN.match(text))


def _reading_of(line: str) -> str | None:
    for block in _P_BLOCK.findall(line):
        candidate = _ANY_TAG.sub("", block).strip()
        if looks_like_pinyin(candidate):
            return candidate
    return None


def parse_dsl(lines: Iterable[str]) -> Iterator[BkrsEntry]:
    """Разобрать поток строк DSL в статьи.

    Заголовки идут без отступа, тело — с отступом. Служебные строки шапки
    (`#NAME`, `#INDEX_LANGUAGE`) и комментарии пропускаются.
    """
    current: BkrsEntry | None = None

    for index, raw in enumerate(lines):
        line = raw.rstrip("\n\r")
        if index == 0:
            # BOM в начале файла: без этого первая директива не начинается с
            # решётки и разбирается как заголовок статьи. Читатели из `_open`
            # его снимают сами, но `parse_dsl` зовут и со списком строк.
            line = line.lstrip("﻿")
        if not line.strip() or line.startswith(_DIRECTIVES):
            continue

        if line[0] in " \t":
            if current is None:
                # Тело без заголовка — битый кусок файла, пропускаем.
                continue

            text = strip_markup(line)
            if not text:
                continue

            # Первая строка тела без разметки и из латиницы — это чтение.
            # Значением она быть не может: русско-китайский словарь не
            # переводит иероглиф латиницей.
            if current.reading is None and not current.senses and looks_like_pinyin(text):
                current.reading = text
                continue

            current.senses.append(text)
            continue

        # Строка без отступа — заголовок.
        headword = strip_markup(line)
        if current is not None and not current.senses:
            # Несколько заголовков подряд — варианты написания одной карточки.
            if headword:
                current.headwords.append(headword)
            continue

        if current is not None and current.usable:
            yield current
        current = BkrsEntry(headwords=[headword] if headword else [])

    if current is not None and current.usable:
        yield current


def download(dest: Path, date: str) -> Path:
    """Скачать ежедневную выгрузку. `date` — в формате ГГММДД."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        log.info("дамп уже на месте: %s", dest)
        return dest

    url = DAILY_URL.format(date=date)
    gz = dest.with_suffix(dest.suffix + ".gz")
    log.info("качаю БКРС: %s", url)
    urllib.request.urlretrieve(url, gz)  # noqa: S310 — адрес собран из константы
    with gzip.open(gz, "rt", encoding="utf-8") as src, dest.open("w", encoding="utf-8") as out:
        for chunk in src:
            out.write(chunk)
    gz.unlink()
    return dest


def usable_headwords(entry: BkrsEntry) -> Iterator[str]:
    """Отобрать заголовки, годные для словаря.

    Дамп содержит и служебные строки, и целые фразы. Латиница без иероглифов
    в китайском словаре бесполезна, длинные фразы карточке не нужны, а
    повторы внутри одной карточки дали бы одинаковые строки в базе.
    """
    seen: set[str] = set()
    for headword in entry.headwords:
        if headword in seen or len(headword) > MAX_HEADWORD_LEN or not _HAN.search(headword):
            continue
        seen.add(headword)
        yield headword


def _open(path: Path):
    """Открыть дамп, распакованный или как есть в `.gz`.

    `utf-8-sig` — не педантизм: BOM приклеился бы к `#NAME`, строка перестала
    бы начинаться с решётки и разобралась бы как заголовок статьи.
    """
    opener = gzip.open if path.suffix == ".gz" else open
    return opener(path, "rt", encoding="utf-8-sig")


def import_file(session: Session, path: Path, *, batch: int = 5000) -> int:
    """Залить дамп в dict_entries, заменив прошлый импорт того же источника."""
    session.execute(delete(DictEntry).where(DictEntry.source == SOURCE))
    session.commit()

    total = 0
    buf: list[dict] = []
    with _open(path) as fh:
        for entry in parse_dsl(fh):
            senses = json.dumps(entry.senses, ensure_ascii=False)
            for headword in usable_headwords(entry):
                buf.append(
                    {
                        "lang": "zh",
                        "headword": headword,
                        "reading": entry.reading,
                        "senses_json": senses,
                        "source": SOURCE,
                    }
                )
            if len(buf) >= batch:
                session.bulk_insert_mappings(DictEntry, buf)
                session.commit()
                total += len(buf)
                buf.clear()

    if buf:
        session.bulk_insert_mappings(DictEntry, buf)
        session.commit()
        total += len(buf)

    log.info("импортировано статей БКРС: %s", total)
    return total

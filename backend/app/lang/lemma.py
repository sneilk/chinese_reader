"""Начальная форма английского слова — для поиска в словаре.

Задача узкая и от этого решаемая. Нам не нужен разбор предложения и не нужна
одна правильная лемма: нужен **список кандидатов**, который `lookup` перебирает
до первого попадания в словарь. Промах кандидата ничего не стоит — статьи
просто нет, — а лишний правильный вариант спасает карточку.

Поэтому здесь нет ни морфологической модели, ни зависимости на неё
(`nltk`, `simplemma`): суффиксные правила плюс таблица неправильных форм
покрывают то, обо что читатель спотыкается, а весит это один файл.

Порядок кандидатов важен ровно в одном месте: **точная форма идёт первой**.
`saw` — это и прошедшее от `see`, и пила; если бы неправильная форма
опережала, читатель получал бы «видеть» там, где в тексте инструмент.

Апострофы разбираются отдельно и по таблице: `don't` → `do` выводится
правилом, а `can't` → `can` и `won't` → `will` — нет.
"""

from __future__ import annotations

import re

_LETTERS = re.compile(r"^[a-z']+$")

_VOWELS = frozenset("aeiou")

# Сокращения. Ключ — форма целиком, значение — то, что имеет смысл искать.
CONTRACTIONS: dict[str, str] = {
    "can't": "can",
    "cannot": "can",
    "won't": "will",
    "shan't": "shall",
    "ain't": "be",
    "let's": "let",
    "'s": "be",
    "'re": "be",
    "'ve": "have",
    "'ll": "will",
    "'d": "would",
    "'m": "be",
}

# Неправильные формы: прошедшее время, причастия, множественное число.
# Список не полный и не должен быть — это те слова, о которые спотыкаются
# на первой же странице, а остальное доберут суффиксные правила.
_IRREGULAR_RAW = """
am are is was were been be
had has have
did done do
went gone go
said say
got gotten get
made make
knew known know
took taken take
saw seen see
came come
thought think
looked look
gave given give
found find
told tell
became become
left leave
felt feel
brought bring
began begun begin
kept keep
held hold
wrote written write
stood stand
heard hear
let let
meant mean
met meet
ran run
paid pay
sat sit
spoke spoken speak
lay lain lie
led lead
grew grown grow
lost lose
fell fallen fall
sent send
built build
understood understand
drew drawn draw
broke broken break
spent spend
cut cut
rose risen rise
drove driven drive
bought buy
wore worn wear
chose chosen choose
caught catch
taught teach
sold sell
fought fight
threw thrown throw
slept sleep
drank drunk drink
ate eaten eat
flew flown fly
forgot forgotten forget
hid hidden hide
struck strike
swore sworn swear
tore torn tear
woke woken wake
won win
shook shaken shake
sank sunk sink
bit bitten bite
hung hang
laid lay
read read
shot shoot
sought seek
bore borne bear
blew blown blow
froze frozen freeze
rode ridden ride
sprang sprung spring
stole stolen steal
swam swum swim
children child
men man
women woman
feet foot
teeth tooth
mice mouse
geese goose
people person
lives life
knives knife
wives wife
leaves leaf
selves self
wolves wolf
halves half
shelves shelf
thieves thief
"""


def _build_irregular() -> dict[str, str]:
    """Строка «формы… база» → отображение каждой формы в базу."""
    table: dict[str, str] = {}
    for line in _IRREGULAR_RAW.strip().splitlines():
        *forms, base = line.split()
        for form in forms:
            table.setdefault(form, base)
    return table


IRREGULAR = _build_irregular()


def _undouble(stem: str) -> str | None:
    """`stopp` → `stop`. Удвоенная согласная перед суффиксом — обычное дело."""
    if len(stem) > 2 and stem[-1] == stem[-2] and stem[-1] not in _VOWELS:
        return stem[:-1]
    return None


def _suffix_forms(word: str) -> list[str]:
    """Кандидаты по суффиксным правилам. Порядок — от вероятного к запасному."""
    out: list[str] = []

    def add(*values: str | None) -> None:
        for value in values:
            if value and len(value) >= 2:
                out.append(value)

    if word.endswith("ies") and len(word) > 4:
        add(word[:-3] + "y", word[:-2])
    elif word.endswith(("ses", "xes", "zes", "ches", "shes")):
        add(word[:-2], word[:-1])
    elif word.endswith("es") and len(word) > 3:
        add(word[:-1], word[:-2])
    elif word.endswith("s") and not word.endswith("ss"):
        add(word[:-1])

    if word.endswith("ied") and len(word) > 4:
        add(word[:-3] + "y")
    elif word.endswith("ed") and len(word) > 3:
        stem = word[:-2]
        add(word[:-1], stem, _undouble(stem))

    if word.endswith("ing") and len(word) > 4:
        stem = word[:-3]
        add(stem, stem + "e", _undouble(stem))

    if word.endswith("est") and len(word) > 4:
        stem = word[:-3]
        add(stem, stem + "e", _undouble(stem))
    elif word.endswith("er") and len(word) > 3:
        stem = word[:-2]
        add(stem, word[:-1], _undouble(stem))

    if word.endswith("ly") and len(word) > 4:
        add(word[:-2], word[:-2] + "e")

    return out


def candidates(word: str) -> list[str]:
    """Формы, под которыми слово может лежать в словаре, — от точной к дальним."""
    word = word.strip().replace("’", "'")
    if not word:
        return []

    ordered: list[str] = [word]
    lower = word.lower()
    if lower != word:
        ordered.append(lower)

    if not _LETTERS.match(lower):
        return _unique(ordered)

    if lower in CONTRACTIONS:
        lower = CONTRACTIONS[lower]
        ordered.append(lower)
    elif lower.endswith("n't") and len(lower) > 3:
        # `don't` — это `do` плюс `n't`, а не `don` плюс `'t`: апостроф стоит
        # **внутри** отрицания, и разрез по нему даёт несуществующее слово.
        # `can't` и `won't` под это правило не подходят вовсе (вышло бы `ca`
        # и `wo`) — они разобраны в таблице выше и сюда не доходят.
        lower = lower[:-3]
        ordered.append(lower)
    elif "'" in lower:
        # Притяжательное `world's` → `world`, а `I'll` → `will`: голова слова
        # осмысленна сама по себе, хвост — только как отдельное сокращение.
        head, _, tail = lower.partition("'")
        ordered.append(head)
        suffix = CONTRACTIONS.get(f"'{tail}")
        if suffix:
            ordered.append(suffix)
        lower = head

    if lower in IRREGULAR:
        ordered.append(IRREGULAR[lower])

    ordered.extend(_suffix_forms(lower))
    return _unique(ordered)


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out

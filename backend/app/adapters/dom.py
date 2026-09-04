"""Разбор HTML без знания вёрстки конкретного сайта.

Адаптер 51shucheng знает свой `#neirong` (T0.5): фикстура снята с живого
сайта, селектор проверен. С novelarrow так нельзя — страница собирается
клиентским Next.js, и селектор, угаданный сегодня, переживёт ровно до
следующей сборки фронта. Поэтому английский адаптер ищет текст **структурно**:
самый плотный по абзацам блок, а не заранее известный контейнер.

Приём такой: взять абзацы, в которых лежит основная масса текста, и вернуть
их **общего предка**. Считать «самый плотный контейнер» простым максимумом
нельзя — `<body>` тоже содержит все абзацы страницы, но вместе с шапкой и
подвалом; а порог вида «почти весь текст» ломается о первый же сайдбар,
который забрал десятую долю знаков. Общий предок от долей не зависит: где
лежит текст, там он и лежит.

Селектор всё же можно задать явно (`novelarrow_content_xpath`): когда живая
страница окажется на руках, точный путь дешевле эвристики, и менять его надо
в конфиге, а не в коде.
"""

from __future__ import annotations

import re

# Ни текста главы, ни навигации — только шум. Выносится целиком с содержимым.
JUNK_TAGS = (
    "script",
    "style",
    "noscript",
    "iframe",
    "ins",
    "svg",
    "form",
    "button",
    "nav",
    "header",
    "footer",
    "aside",
    "template",
)

# Какую долю всего текста страницы должны покрыть абзацы, по которым ищется
# общий предок. Две трети — запас на то, что часть абзацев главы окажется
# короче сайдбарных, и на подписи под иллюстрациями.
_TEXT_SHARE = 2 / 3

# Короче этого — подпись, пункт меню или кнопка, а не абзац главы.
MIN_PARAGRAPH_CHARS = 20

# Подстановка под <br>. Обычным переводом строки её делать нельзя: в
# отформатированном HTML переводы строк стоят и **внутри** абзацев, где они
# всего лишь пробел, и глава рассыпалась бы по ширине исходника.
# Знак из области частного использования, а не управляющий код: последние
# lxml в текст узла не пускает вовсе.
_BREAK = "\ue000"

_NEXT_TEXTS = re.compile(
    r"^(next|next\s*chapter|next\s*chap|next\s*page|continue|下一[章页节])\b|^[»›→]+$",
    re.IGNORECASE,
)
_PREV_HINT = re.compile(r"prev|previous|上一", re.IGNORECASE)
_NEXT_HINT = re.compile(r"next", re.IGNORECASE)


def drop_junk(element) -> None:
    """Вынести из поддерева скрипты, рекламу и навигацию — вместе с текстом."""
    for tag in JUNK_TAGS:
        for el in element.findall(f".//{tag}"):
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)


def _ancestry(element) -> list:
    """Цепочка от корня до элемента включительно."""
    chain = [element]
    parent = element.getparent()
    while parent is not None:
        chain.append(parent)
        parent = parent.getparent()
    chain.reverse()
    return chain


def _common_ancestor(elements: list):
    """Самый глубокий общий предок. `None` — общего предка нет вовсе."""
    shared = _ancestry(elements[0])
    for element in elements[1:]:
        chain = _ancestry(element)
        limit = min(len(shared), len(chain))
        cut = 0
        while cut < limit and shared[cut] is chain[cut]:
            cut += 1
        shared = shared[:cut]
        if not shared:
            return None
    return shared[-1]


def densest_block(doc):
    """Найти блок, в котором лежит текст главы. `None` — абзацев на странице нет.

    Берутся самые длинные абзацы, пока они не покроют основную массу текста
    страницы, и возвращается их общий предок. Сайдбар и подвал в эту массу не
    попадают, поэтому предок опускается до контейнера главы сам, без порогов
    и без знания вёрстки.
    """
    scored: list[tuple[int, object]] = []
    for p in doc.iter("p"):
        text = " ".join(p.text_content().split())
        if len(text) >= MIN_PARAGRAPH_CHARS:
            scored.append((len(text), p))

    if not scored:
        return None

    total = sum(size for size, _ in scored)
    scored.sort(key=lambda pair: pair[0], reverse=True)

    chosen: list = []
    covered = 0
    for size, element in scored:
        chosen.append(element)
        covered += size
        if covered >= total * _TEXT_SHARE:
            break

    block = _common_ancestor(chosen)
    if block is None:
        return None
    # Основную массу текста может держать и один абзац — короткая глава,
    # вставка целиком. Сам по себе он не блок: остальные абзацы главы лежат
    # рядом с ним, а не внутри.
    return block.getparent() if block.tag == "p" and block.getparent() is not None else block


def block_paragraphs(element) -> list[str]:
    """Вытащить абзацы из блока: `<p>`, а если их нет — строки через `<br>`.

    `<br>` заменяется подстановкой заранее: `text_content()` его просто
    выбрасывает, и глава, свёрстанная переносами, склеилась бы в один абзац
    на три тысячи слов — то есть в одно предложение для переводчика.

    Собственные переводы строк исходника при этом схлопываются в пробел, как
    и положено в HTML: в отформатированной разметке они стоят посреди абзацев
    и границами не являются.
    """
    for br in element.iter("br"):
        br.tail = _BREAK + (br.tail or "")

    blocks = element.findall(".//p")
    lines = [b.text_content() for b in blocks] if blocks else [element.text_content()]

    out: list[str] = []
    for line in lines:
        for piece in line.split(_BREAK):
            piece = " ".join(piece.split())
            if piece:
                out.append(piece)
    return out


def find_next_link(doc, exclude: str | None = None) -> str | None:
    """Найти ссылку «следующая глава». `None` — её на странице нет.

    Четыре попытки по убыванию надёжности: объявленный `rel="next"`, подпись
    ссылки, `aria-label` или `title`, класс с идентификатором. Последняя
    проверка отдельно отсеивает «предыдущую»: у пары кнопок класс часто общий
    (`nav-btn prev`, `nav-btn next`), и без исключения книга поехала бы назад.

    Третья попытка появилась по живой странице novelarrow: кнопка вперёд там —
    голая стрелка в `<svg>`, то есть ссылка **без текста вовсе**. Подпись у неё
    только одна, в `aria-label="Next chapter"`, а классы сгенерированы Tailwind
    и слова `next` не содержат. Без этой попытки обход книги на живом сайте не
    начинался: ссылки нет — цепочка кончилась, ещё не начавшись.
    """
    for href in doc.xpath("//link[@rel='next']/@href | //a[@rel='next']/@href"):
        if href and href != exclude:
            return str(href)

    labelled: str | None = None
    fallback: str | None = None
    for anchor in doc.iter("a"):
        href = anchor.get("href")
        if not href or href.startswith(("#", "javascript:")) or href == exclude:
            continue

        text = " ".join(anchor.text_content().split())
        if _NEXT_TEXTS.match(text):
            return href

        label = f"{anchor.get('aria-label', '')} {anchor.get('title', '')}".strip()
        if labelled is None and _NEXT_TEXTS.match(label) and not _PREV_HINT.search(label):
            labelled = href
            continue

        marks = f"{anchor.get('class', '')} {anchor.get('id', '')} {label}"
        if fallback is None and _NEXT_HINT.search(marks) and not _PREV_HINT.search(marks):
            fallback = href

    return labelled or fallback


def page_title(doc) -> str:
    """Заголовок главы: `<h1>`, иначе `<title>` без хвоста с именем сайта."""
    for el in doc.iter("h1"):
        text = " ".join(el.text_content().split())
        if text:
            return text

    node = doc.find(".//title")
    raw = " ".join((node.text or "").split()) if node is not None else ""
    # «Chapter 12 - Название книги | Сайт» — от заголовка главы отделяем
    # только хвост с именем сайта: он всюду одинаковый и в главе не нужен.
    return raw.split("|")[0].strip() or raw

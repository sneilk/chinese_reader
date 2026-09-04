"""Адаптер 无忧书城 (51shucheng.net).

Структура страницы главы, снятая с живой фикстуры (T0.5):

    div.page
      h1.chapter-title          — заголовок главы
      div.reading-container
        div#neirong             — текст: прямые <p>, плюс реклама и скрипты
      nav
        a#BookHome              — «☰目录», оглавление книги
        a#BookNext              — «下一章 ›», следующая глава
      div#toc-container
        ul#toc-list             — список всех глав книги

`#toc-list` — это те самые ~40% иероглифов, которые нельзя отдавать
переводчику: за них платят, а читать их никто не будет.

Отсутствие `#neirong` — надёжный признак того, что перед нами не глава:
именно так выглядит «мягкий 404», когда сайт на несуществующий номер главы
отдаёт 200 и страницу оглавления книги.

## Ссылка вперёд у сайта есть, и адаптер обязан её отдавать

Кнопка «下一章» стоит на каждой странице главы и несёт абсолютный адрес
следующей. Пока адаптер её не читал, `next_chapter_url` у китайских глав был
пуст всегда — а значит обход книги (`pipeline.walk_chapters`) на 51shucheng не
делал ни шага, и «Загрузить следующую» в читалке не появлялась вовсе.
Выглядело это как «у сайта нет ссылки вперёд», хотя она есть и в разметке, и
на экране.
"""

from urllib.parse import urlparse

from lxml import html as lh

from app.adapters.base import AdapterFailure, ChapterRaw, han_count, require_text
from app.adapters.dom import find_next_link
from app.domain import ErrorKind, Language

_JUNK_TAGS = ("script", "style", "ins", "iframe", "noscript")

# Глава книги — это всегда `/{жанр}/{книга}/{номер}.html` (sources.md §1).
_CHAPTER_SUFFIX = ".html"


class ShuchengAdapter:
    name = "51shucheng"
    lang = Language.ZH

    def matches(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host == "51shucheng.net" or host.endswith(".51shucheng.net")

    def parse_chapter(self, html: str, url: str) -> ChapterRaw:
        doc = lh.fromstring(html)

        body = doc.get_element_by_id("neirong", None)
        if body is None:
            raise AdapterFailure(
                ErrorKind.EMPTY_EXTRACT,
                "на странице нет #neirong — это не глава, а, скорее всего, оглавление книги",
            )

        # Реклама и скрипты живут прямо внутри контейнера текста.
        for tag in _JUNK_TAGS:
            for el in body.findall(f".//{tag}"):
                el.getparent().remove(el)

        paragraphs = []
        for p in body.findall(".//p"):
            text = " ".join(p.text_content().split())
            if han_count(text):
                paragraphs.append(text)

        chapter = ChapterRaw(
            title=self._title(doc),
            paragraphs=paragraphs,
            lang=self.lang,
            next_chapter_url=self._next_chapter(doc, url),
        )
        return require_text(chapter, "#neirong есть, но пуст")

    @staticmethod
    def _next_chapter(doc, url: str) -> str | None:
        """Адрес следующей главы книги. `None` — книга кончилась.

        Своя кнопка надёжнее эвристики: `#BookNext` стоит в разметке сайта, а
        не угадывается по подписи. Зато и проверять её приходится — на
        последней главе кнопка ведёт на оглавление книги, а не вперёд, и
        отличается это ровно тем, что адрес перестаёт быть адресом главы.

        Если кнопки нет вовсе — разметка сайта поменялась, и тогда общий поиск
        по подписи и классам всё-таки лучше молчаливого «ссылки не нашлось».
        """
        anchors = doc.xpath("//a[@id='BookNext']")
        if anchors:
            href = (anchors[0].get("href") or "").strip()
            if href and href != url and href.endswith(_CHAPTER_SUFFIX):
                return href
            return None
        return find_next_link(doc, exclude=url)

    @staticmethod
    def _title(doc) -> str:
        for el in doc.find_class("chapter-title"):
            text = " ".join(el.text_content().split())
            if text:
                return text
        # Запасной вариант: <title> вида «глава_книга - сайт».
        node = doc.find(".//title")
        raw = " ".join((node.text or "").split()) if node is not None else ""
        return raw.split("_", 1)[0].strip()

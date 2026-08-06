"""Адаптер 无忧书城 (51shucheng.net).

Структура страницы главы, снятая с живой фикстуры (T0.5):

    div.page
      h1.chapter-title          — заголовок главы
      div.reading-container
        div#neirong             — текст: прямые <p>, плюс реклама и скрипты
      div#toc-container
        ul#toc-list             — список всех глав книги

`#toc-list` — это те самые ~40% иероглифов, которые нельзя отдавать
переводчику: за них платят, а читать их никто не будет.

Отсутствие `#neirong` — надёжный признак того, что перед нами не глава:
именно так выглядит «мягкий 404», когда сайт на несуществующий номер главы
отдаёт 200 и страницу оглавления книги.
"""

from urllib.parse import urlparse

from lxml import html as lh

from app.adapters.base import MIN_CHAPTER_HAN, AdapterFailure, ChapterRaw, han_count
from app.domain import ErrorKind

_JUNK_TAGS = ("script", "style", "ins", "iframe", "noscript")


class ShuchengAdapter:
    name = "51shucheng"

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

        chapter = ChapterRaw(title=self._title(doc), paragraphs=paragraphs)
        if chapter.han < MIN_CHAPTER_HAN:
            raise AdapterFailure(
                ErrorKind.EMPTY_EXTRACT,
                f"#neirong есть, но текста в нём {chapter.han} иероглифов",
            )
        return chapter

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

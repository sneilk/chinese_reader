"""Адаптер novelarrow.com — английские переводы веб-новелл.

Что о сайте известно из разведки ([sources.md](../../../docs/sources.md) §2):
Next.js с серверным рендером за Cloudflare, страница новеллы отдаётся без JS,
а **список глав грузится клиентом** — в HTML его нет. Внутренний `/api/` для
нас закрыт: он явно запрещён в `robots.txt`, и ходить туда мы не будем.

Отсюда два следствия, определяющие весь этот файл.

**Книга обходится по ссылке «следующая глава», а не по оглавлению.** Другого
входа нет: оглавления в разметке не существует, а собирать адреса «по схеме»
на Next.js бессмысленно — слаг главы не выводится из номера. Поэтому адаптер
обязан находить `next_chapter_url`, и это его вторая по важности работа после
самого текста.

**Селектор текста не зашит.** У 51shucheng есть `#neirong`, снятый с живой
фикстуры и проверенный; здесь такого нет и быть не может — классы Next.js
генерируются сборкой и меняются вместе с ней. Поэтому текст ищется
структурно, самым плотным по абзацам блоком (`dom.densest_block`), а точный
XPath можно задать в конфиге, когда живая страница окажется на руках.

Запасной путь — trafilatura, тот же движок, что у generic-адаптера. Он
срабатывает, когда абзацев в разметке нет вовсе: у ридеров встречается
вёрстка переносами внутри одного `<div>`.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from lxml import html as lh

from app.adapters.base import ChapterRaw, require_text
from app.adapters.dom import block_paragraphs, densest_block, drop_junk, find_next_link, page_title
from app.config import settings
from app.domain import Language

log = logging.getLogger(__name__)


class NovelarrowAdapter:
    name = "novelarrow"
    lang = Language.EN

    def matches(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host == "novelarrow.com" or host.endswith(".novelarrow.com")

    def parse_chapter(self, html: str, url: str) -> ChapterRaw:
        doc = lh.fromstring(html)
        # Ссылку ищем до вычистки: `nav` и `footer` уезжают вместе с кнопками
        # перехода, а именно там они чаще всего и стоят.
        next_chapter = find_next_link(doc, exclude=url)
        title = page_title(doc)

        drop_junk(doc)
        paragraphs = self._paragraphs(doc, html, url)

        chapter = ChapterRaw(
            title=title,
            paragraphs=paragraphs,
            lang=self.lang,
            next_chapter_url=next_chapter,
        )
        return require_text(chapter, "страница загрузилась, но главы в ней нет")

    def _paragraphs(self, doc, html: str, url: str) -> list[str]:
        """Абзацы главы: явный селектор, плотный блок, trafilatura — в этом порядке."""
        if settings.novelarrow_content_xpath:
            found = doc.xpath(settings.novelarrow_content_xpath)
            if found:
                return block_paragraphs(found[0])
            log.warning("novelarrow: novelarrow_content_xpath ничего не нашёл, беру эвристику")

        block = densest_block(doc)
        if block is not None:
            paragraphs = block_paragraphs(block)
            if paragraphs:
                return paragraphs

        return _trafilatura_paragraphs(html, url)


def _trafilatura_paragraphs(html: str, url: str) -> list[str]:
    """Запасной путь для страниц без абзацев в разметке."""
    import trafilatura

    text = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
    )
    if not text:
        return []
    return [line for line in (" ".join(x.split()) for x in text.splitlines()) if line]

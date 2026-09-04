"""Снять живые страницы глав со всех источников.

    PYTHONPATH=. python scripts/snapshot_fixtures.py
    PYTHONPATH=. python scripts/snapshot_fixtures.py 51shucheng   # только один

Зачем это нужно отдельным скриптом, а не фикстурой в репозитории. Структурные
фикстуры в `tests/fixtures/` повторяют форму страницы, но пишутся руками — а
значит показывают то, что мы **думаем** о разметке сайта. Ровно на этом
разошлись обе ссылки «следующая глава»: у 51shucheng кнопка `#BookNext` была в
разметке всегда, а адаптер её не читал; у novelarrow она оказалась голой
стрелкой в `<svg>` с подписью только в `aria-label`, чего ни одна рукописная
фикстура не предполагала.

Живые страницы в git не едут: это чужие произведения. Они ложатся в
`data/fixtures/` (каталог вне git), а тесты, которым они нужны, пропускаются,
если файла нет. Пересниматься набор должен осознанно — сайт меняет вёрстку, и
расхождение фикстуры с реальностью надо увидеть diff'ом, а не отказом в бою.

Загрузка идёт тем же `BrowserFetcher`, что и в сервисе, и по той же причине:
51shucheng закрыт Cloudflare, и headless получает 403 даже с живыми куками
(T0.3). Значит нужен экран — на разработческой машине свой, на ВМ Xvfb.
"""

from __future__ import annotations

import asyncio
import json
import sys

from app.config import settings
from app.fetchers.base import FetchFailure
from app.fetchers.browser import BrowserFetcher

#: Что снимаем. Ключ — имя файла в `data/fixtures/`, значение — адрес.
#: Адреса подобраны так, чтобы покрыть все три вопроса к странице: есть ли
#: текст, есть ли ссылка вперёд и ведёт ли она туда, где ссылка тоже есть.
PAGES: dict[str, str] = {
    "novelarrow-chapter": "https://novelarrow.com/chapter/shadow-slave/chapter-1-nightmare-begins",
}

#: Адрес китайской главы известен только на этой машине: он лежит в
#: `data/target.json` рядом с остальными данными и в репозиторий не едет.
TARGET = settings.data_dir / "target.json"


def shucheng_pages() -> dict[str, str]:
    """Глава 51shucheng и следующая за ней. Пусто, если адрес не задан."""
    if not TARGET.exists():
        print(f"нет {TARGET} — китайские страницы пропускаю", file=sys.stderr)
        return {}

    url = json.loads(TARGET.read_text(encoding="utf-8"))["chapter_url"]
    head, tail = url.rsplit("/", 1)
    number = int(tail.split(".")[0])
    return {
        "51shucheng-chapter": url,
        # Вторая глава нужна не для полноты: только на ней видно, что ссылка
        # вперёд ведёт вперёд. На первой главе кнопки «предыдущая» нет вовсе,
        # и перепутать их там нечем.
        "51shucheng-chapter-next": f"{head}/{number + 1}.html",
    }


async def main(only: str | None) -> int:
    pages = {**shucheng_pages(), **PAGES}
    if only:
        pages = {name: url for name, url in pages.items() if name.startswith(only)}
        if not pages:
            print(f"нечего снимать по образцу {only!r}", file=sys.stderr)
            return 2

    out = settings.data_dir / "fixtures"
    out.mkdir(parents=True, exist_ok=True)

    failed = 0
    async with BrowserFetcher() as fetcher:
        for name, url in pages.items():
            try:
                result = await fetcher.get(url)
            except FetchFailure as e:
                print(f"{name}: не снялась — {e.kind}: {e.detail[:120]}", file=sys.stderr)
                failed += 1
                continue

            path = out / f"{name}.html"
            path.write_text(result.html, encoding="utf-8")
            print(f"{name}: {len(result.html)} байт, {result.title[:60]!r} → {path}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None)))

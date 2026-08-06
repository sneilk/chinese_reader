"""Загрузчик на реальном браузере.

Почему именно так, а не проще (проверено в T0.3, см. tasks-mvp.md):

* **headless не работает** — Cloudflare отдаёт 403 даже когда в профиле лежит
  живая `cf_clearance`. Нужен headful под Xvfb;
* **профиль обязан быть постоянным** — куки переживают и перезапуск браузера,
  и перезагрузку машины, поэтому челлендж проходится один раз, а не каждый раз;
* **браузер поднимается один раз** на всё время работы: старт стоит секунды,
  а глав за сессию читается много.

Вежливость к сайту (§1.3 концепции) обеспечивается здесь же: одна загрузка
за раз и пауза между запросами.
"""

import asyncio
import contextlib
import logging
import time

from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from app.config import settings
from app.domain import ErrorKind
from app.fetchers.base import FetchFailure, FetchResult, classify

log = logging.getLogger(__name__)


class BrowserFetcher:
    """Playwright с постоянным профилем. Один экземпляр на приложение."""

    def __init__(
        self,
        profile_dir=None,
        *,
        headless: bool | None = None,
        nav_timeout_ms: int | None = None,
        retries: int | None = None,
        delay_seconds: float | None = None,
    ) -> None:
        self._profile_dir = profile_dir or settings.browser_profile_dir
        self._headless = settings.browser_headless if headless is None else headless
        self._nav_timeout = nav_timeout_ms or settings.browser_nav_timeout_ms
        self._retries = settings.browser_retries if retries is None else retries
        self._delay = settings.fetch_delay_seconds if delay_seconds is None else delay_seconds

        self._pw = None
        self._ctx: BrowserContext | Browser | None = None
        # Параллелизм 1: и ради вежливости, и потому что один профиль нельзя
        # открыть двумя контекстами сразу.
        self._lock = asyncio.Lock()
        self._last_request_at = 0.0

    async def start(self) -> None:
        if self._ctx is not None:
            return
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = await async_playwright().start()
        self._ctx = await self._pw.chromium.launch_persistent_context(
            str(self._profile_dir),
            headless=self._headless,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1280, "height": 800},
            args=["--disable-blink-features=AutomationControlled"],
        )
        log.info("браузер запущен, профиль %s, headless=%s", self._profile_dir, self._headless)

    async def close(self) -> None:
        if self._ctx is not None:
            await self._ctx.close()
            self._ctx = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    async def __aenter__(self) -> "BrowserFetcher":
        await self.start()
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.close()

    async def _throttle(self) -> None:
        gap = time.monotonic() - self._last_request_at
        if gap < self._delay:
            await asyncio.sleep(self._delay - gap)
        self._last_request_at = time.monotonic()

    async def get(self, url: str) -> FetchResult:
        """Загрузить страницу. Бросает FetchFailure с внятной причиной."""
        if self._ctx is None:
            await self.start()

        last: FetchFailure | None = None
        # Первая попытка плюс ретраи: паузы растут, чтобы не долбить сайт.
        for attempt in range(self._retries + 1):
            if attempt:
                await asyncio.sleep(5 * attempt * attempt)
            async with self._lock:
                await self._throttle()
                try:
                    return await self._attempt(url)
                except FetchFailure as e:
                    last = e
                    # Отсутствие страницы ретраем не лечится.
                    if e.kind is ErrorKind.NOT_FOUND:
                        raise
                    log.warning("попытка %s для %s: %s", attempt + 1, url, e.kind)

        assert last is not None
        raise last

    async def _attempt(self, url: str) -> FetchResult:
        assert self._ctx is not None
        page: Page = await self._ctx.new_page()
        try:
            try:
                resp = await page.goto(
                    url, wait_until="domcontentloaded", timeout=self._nav_timeout
                )
            except PlaywrightTimeout as e:
                raise FetchFailure(ErrorKind.FETCH_TIMEOUT, str(e)[:200]) from e
            except PlaywrightError as e:
                raise FetchFailure(ErrorKind.ADAPTER_ERROR, str(e)[:200]) from e

            if resp is None:
                raise FetchFailure(ErrorKind.ADAPTER_ERROR, "навигация не вернула ответ")

            # Сеть в покое, но недолго: на некоторых страницах она не затихает
            # никогда, и это не повод считать загрузку неудачной.
            with contextlib.suppress(PlaywrightTimeout):
                await page.wait_for_load_state("networkidle", timeout=15_000)

            title = await page.title()
            kind = classify(resp.status, title, await resp.all_headers())
            if kind is not None:
                raise FetchFailure(kind, f"HTTP {resp.status}, заголовок {title!r}")

            return FetchResult(
                url=page.url, status=resp.status, html=await page.content(), title=title
            )
        finally:
            await page.close()

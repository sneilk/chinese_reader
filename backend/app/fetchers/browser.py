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

## Окно, в котором проверку проходят руками

Обычно челлендж проходится сам — это проверено и на ВМ, и на живых главах. Но
«обычно» не значит «всегда»: Cloudflare вправе показать капчу, а капча требует
рук. Пока увидеть её было нельзя, любой такой случай выглядел одинаково —
глава в `failed` с `challenge` и советом «попробуйте через минуту», который в
этом случае не помогает никогда.

`open_for_check` открывает страницу и **оставляет её открытой**: браузер уже
headful, окно уже есть — на разработческой машине оно просто появляется на
экране, на ВМ живёт на Xvfb и смотрится через VNC. Пока окно открыто, проверку
можно пройти руками, а метод ждёт и сам замечает, что она прошла.

Снимок экрана делается в любом случае и кладётся файлом: он отвечает на
вопрос, ради которого всё и затевалось, — что сейчас на экране у браузера.
Через него видно и капчу, и то, что её нет.
"""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from app.config import settings
from app.domain import ErrorKind
from app.fetchers.base import FetchFailure, FetchResult, classify

log = logging.getLogger(__name__)

#: Как часто пересматривать открытую страницу, ожидая, что проверка прошла.
_CHECK_POLL_SECONDS = 2.0


@dataclass(frozen=True)
class _Seen:
    """Один взгляд на открытую страницу: что на ней и чем это пахнет."""

    kind: ErrorKind | None
    status: int
    title: str
    headers: dict[str, str]


@dataclass(frozen=True)
class CheckResult:
    """Чем кончилась ручная проверка сайта."""

    #: Страница отдалась как надо: ни челленджа, ни отказа.
    ok: bool
    #: Причина, если не отдалась. `None` — всё в порядке.
    kind: ErrorKind | None
    status: int
    title: str
    #: Адрес, на котором браузер в итоге оказался.
    url: str
    #: Сколько ждали. По нему видно, прошла проверка сама или её проходили.
    waited_seconds: float
    #: Видно ли окно человеку. False — браузер headless, проходить нечего.
    visible: bool
    #: Снимок экрана. `None` — снять не удалось, и это не отменяет проверку.
    screenshot: Path | None


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

    async def open_for_check(
        self,
        url: str,
        *,
        seconds: float | None = None,
        screenshot_path: Path | None = None,
    ) -> CheckResult:
        """Открыть страницу и держать окно, пока проверку проходят руками.

        Ждёт не вслепую: каждые пару секунд смотрит на страницу заново и
        возвращается сразу, как только челленджа не стало. Поэтому обычный
        случай — «проверка прошла сама» — стоит секунды, а не всё окно
        ожидания.

        Ждём **только челлендж**, и это важно. У 404 и «доступ закрыт» ждать
        нечего: они не пройдут ни сами, ни руками, и минута ожидания на них —
        просто минута.

        Замок загрузчика удерживается всё это время намеренно. Ходить на сайт,
        пока на нём же открыта проверка, — верный способ получить второй
        челлендж; да и профиль у браузера один, и куку, ради которой всё
        затевалось, писать в него должен кто-то один.
        """
        if self._ctx is None:
            await self.start()
        assert self._ctx is not None

        wait = settings.browser_check_timeout_seconds if seconds is None else seconds
        started = time.monotonic()

        async with self._lock:
            page: Page = await self._ctx.new_page()
            try:
                seen = await self._look(page, goto=url)
                while seen.kind is ErrorKind.CHALLENGE and time.monotonic() - started < wait:
                    await asyncio.sleep(_CHECK_POLL_SECONDS)
                    seen = await self._relook(page)

                kind, status, title = seen.kind, seen.status, seen.title
                shot = await self._screenshot(page, screenshot_path)
                waited = time.monotonic() - started
                log.info(
                    "ручная проверка %s: %s за %.0f с, заголовок %r",
                    url,
                    "прошла" if kind is None else kind,
                    waited,
                    title[:80],
                )
                return CheckResult(
                    ok=kind is None,
                    kind=kind,
                    status=status,
                    title=title,
                    url=page.url,
                    waited_seconds=waited,
                    visible=not self._headless,
                    screenshot=shot,
                )
            finally:
                await page.close()

    async def _look(self, page: Page, *, goto: str) -> _Seen:
        """Перейти по адресу и рассудить, что получили."""
        try:
            resp = await page.goto(goto, wait_until="domcontentloaded", timeout=self._nav_timeout)
            status = resp.status if resp is not None else 0
            headers = await resp.all_headers() if resp is not None else {}
            title = await page.title()
        except PlaywrightTimeout:
            return _Seen(ErrorKind.FETCH_TIMEOUT, 0, "", {})
        except PlaywrightError as e:
            return _Seen(ErrorKind.ADAPTER_ERROR, 0, str(e)[:120], {})

        return _Seen(classify(status, title, headers), status, title, headers)

    async def _relook(self, page: Page) -> _Seen:
        """Посмотреть на уже открытую страницу ещё раз. Судим по заголовку окна.

        Второй раз переходить по тому же адресу нельзя: это снесло бы всё, что
        человек успел нажать, и проверка начиналась бы заново каждые две
        секунды.

        А раз перехода нет, то нет и ответа: статус и `cf-mitigated` остались
        от первого запроса и **никогда не изменятся**. Судить по ним — значит
        не заметить пройденную проверку вообще: страница уже показывает главу,
        а в руках у нас всё ещё 403 с `cf-mitigated: challenge`, по которому
        `classify` честно ответит «челлендж». Поэтому здесь спрашивается
        единственное, что действительно меняется, — заголовок окна.

        Статус ставится успешным нарочно: сюда попадают только после
        распознанного челленджа, а его исход весь в заголовке.
        """
        try:
            title = await page.title()
        except PlaywrightTimeout:
            return _Seen(ErrorKind.FETCH_TIMEOUT, 0, "", {})
        except PlaywrightError as e:
            return _Seen(ErrorKind.ADAPTER_ERROR, 0, str(e)[:120], {})

        return _Seen(classify(200, title, {}), 200, title, {})

    @staticmethod
    async def _screenshot(page: Page, path: Path | None) -> Path | None:
        """Снимок открытой страницы. Отказ снимка не отменяет саму проверку."""
        if path is None:
            return None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(path), full_page=False)
        except PlaywrightError as e:
            log.warning("снимок экрана не получился: %s", str(e)[:120])
            return None
        return path

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

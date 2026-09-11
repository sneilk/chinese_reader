"""Перевод предложений через Яндекс.Переводчик.

Решения зафиксированы в [translation.md](../../../docs/translation.md), здесь
только то, что определяет код:

* **одно предложение — один элемент `texts[]`**, ответ приходит в том же
  порядке. Выравнивание с `sentences.idx` получается бесплатно, и переведённый
  абзац не приходится резать обратно на предложения (§2);
* **язык оригинала — параметр вызова, а не константа клиента**: направление
  задаёт глава (`chapters.lang`), и один и тот же клиент переводит и `zh→ru`,
  и `en→ru`. Целевой язык при этом константа: читатель один и русский;
* батч — до 10 000 символов суммарно, дефолт в конфиге взят с запасом (§6);
* глава переводится целиком при загрузке: при цене около 2,5 ₽ за главу
  экономия на непрочитанных предложениях не стоит задержки на каждый тап (§3);
* глоссарий в MVP не используется — на терминах с верным дефолтным переводом
  он задваивает результат (T0.6), поэтому в него должны попадать только слова
  с заведомо неверным переводом, а таких пока неоткуда взять;
* считаем `chars_sent` на каждый запрос: тарификация посимвольная, и «сколько
  стоит месяц чтения» должно быть измеримо, а не оценочно (§7).

Отказ переводчика — не потеря главы: она остаётся на `segmented` и читается
без переводов (RFC §4). Поэтому наверх уходит одна внятная причина, а не
россыпь HTTP-кодов.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import settings
from app.domain import TARGET_LANGUAGE, ErrorKind, Language

log = logging.getLogger(__name__)

API_URL = "https://translate.api.cloud.yandex.net/translate/v2/translate"

#: Направление по умолчанию — китайский, с которого всё начиналось.
SOURCE_LANG = Language.ZH
TARGET_LANG = TARGET_LANGUAGE

# Пустой запрос тарифицируется как один символ (translation.md §3), поэтому
# считаем так же — иначе учёт разойдётся со счётом.
MIN_BILLED_CHARS = 1

# Коды, при которых имеет смысл повторить: перегрузка и таймауты на той
# стороне. Всё остальное — наша ошибка, и ретрай её не вылечит.
RETRIABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class TranslateResult:
    """Переводы в порядке входа плюс то, за что заплачено.

    `texts` может быть **короче** запрошенного: это успевшая часть, когда
    отказ случился на середине. Порядок при этом сохранён, поэтому такой
    результат ложится ровно на первые `len(texts)` предложений и ни на какие
    другие.
    """

    texts: list[str]
    chars_sent: int
    requests: int


class TranslateFailure(Exception):
    """Перевод не удался. Причина одна: глава при этом остаётся читаемой.

    `partial` — то, что провайдер успел вернуть **до** отказа, и за что уже
    выставлен счёт. Раньше эта часть просто выбрасывалась вместе с
    исключением, и цена ошибки была тройной: переводы терялись, потраченные
    деньги не попадали ни в один счётчик, а повтор переотправлял оплаченное
    заново. `None` — терять нечего: не дошли до провайдера вовсе, либо ответ
    пришёл битым и доверять ему нельзя.
    """

    kind = ErrorKind.TRANSLATE_FAILED

    def __init__(self, detail: str, partial: TranslateResult | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.partial = partial


class Translator(Protocol):
    """Смена провайдера должна стоить один файл (концепция §4.2)."""

    async def translate(
        self, texts: Sequence[str], *, source: str = SOURCE_LANG
    ) -> TranslateResult: ...


def make_batches(texts: Sequence[str], limit: int) -> list[list[str]]:
    """Разложить предложения по батчам, не превышая лимит символов на запрос.

    Порядок сохраняется и внутри батча, и между ними: на нём держится
    раскладка ответа по `sentences.idx`.

    Предложение длиннее лимита уезжает в запрос в одиночку — резать его мы не
    имеем права, потому что перевод половины фразы бессмыслен. После резки на
    предложения таких быть не должно, поэтому случай логируется.
    """
    batches: list[list[str]] = []
    current: list[str] = []
    used = 0

    for text in texts:
        size = len(text)
        if size > limit:
            log.warning("предложение длиннее лимита (%s > %s), уходит отдельно", size, limit)
            if current:
                batches.append(current)
                current, used = [], 0
            batches.append([text])
            continue
        if current and used + size > limit:
            batches.append(current)
            current, used = [], 0
        current.append(text)
        used += size

    if current:
        batches.append(current)
    return batches


def billed_chars(texts: Sequence[str]) -> int:
    return sum(max(len(t), MIN_BILLED_CHARS) for t in texts)


class YandexTranslate:
    """Клиент Яндекс.Переводчика.

    Ключ живёт только на сервере, в переменных окружения (§6): в клиенте он
    был бы равносилен публичному.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        folder_id: str | None = None,
        batch_chars: int | None = None,
        timeout: float | None = None,
        retries: int | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else settings.yc_translate_api_key
        self._folder_id = folder_id if folder_id is not None else settings.yc_folder_id
        self._batch_chars = batch_chars or settings.translate_batch_chars
        self._timeout = timeout or settings.translate_timeout_seconds
        self._retries = settings.translate_retries if retries is None else retries
        self._client = client
        self._own_client = client is None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._client is not None and self._own_client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> YandexTranslate:
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.close()

    async def translate(
        self, texts: Sequence[str], *, source: str = SOURCE_LANG
    ) -> TranslateResult:
        """Перевести предложения. Бросает TranslateFailure с одной причиной.

        Глава длиннее батча уезжает несколькими запросами, и каждый из них
        тарифицируется отдельно. Поэтому отказ на втором запросе **не
        отменяет первый**: за него уже выставлен счёт, и выбросить его
        значило бы заплатить дважды за один и тот же текст. Успевшая часть
        уходит наверх вместе с исключением (`TranslateFailure.partial`), а
        конвейер её сохраняет — после чего повтор досылает только недостающее.

        Длину ответа сверяет `_translate_batch`, побатчно. Это важно именно
        здесь: расхождение молча испортило бы раскладку по предложениям —
        сдвиг на один, и вся глава переведена «не про то», — а по итоговой
        сумме нельзя понять, какой из запросов ответил не тем. Побатчная
        сверка и находит виноватого, и оставляет в силе всё, что было до него.
        """
        if not texts:
            return TranslateResult(texts=[], chars_sent=0, requests=0)
        if not self._api_key or not self._folder_id:
            raise TranslateFailure("не задан ключ или каталог Яндекс.Переводчика")

        out: list[str] = []
        chars = 0
        done = 0
        batches = make_batches(texts, self._batch_chars)

        for batch in batches:
            try:
                # Длину ответа сверяет сам `_translate_batch`: расхождение —
                # это отказ этого запроса, а не итога, и всё, что пришло до
                # него, остаётся в силе.
                translated = await self._translate_batch(batch, source)
            except TranslateFailure as e:
                if not out:
                    raise
                raise TranslateFailure(
                    e.detail, partial=TranslateResult(texts=out, chars_sent=chars, requests=done)
                ) from e

            out.extend(translated)
            chars += billed_chars(batch)
            done += 1

        return TranslateResult(texts=out, chars_sent=chars, requests=done)

    async def _translate_batch(self, batch: Sequence[str], source: str) -> list[str]:
        client = await self._get_client()
        body = {
            "folderId": self._folder_id,
            "texts": list(batch),
            "sourceLanguageCode": str(source),
            "targetLanguageCode": TARGET_LANG,
            "format": "PLAIN_TEXT",
        }
        headers = {"Authorization": f"Api-Key {self._api_key}"}

        last = ""
        for attempt in range(self._retries + 1):
            if attempt:
                await asyncio.sleep(self._backoff(attempt))
            started = time.monotonic()
            try:
                resp = await client.post(API_URL, json=body, headers=headers)
            except httpx.TimeoutException as e:
                last = f"таймаут: {e}"
                log.warning("перевод, попытка %s: %s", attempt + 1, last)
                continue
            except httpx.HTTPError as e:
                raise TranslateFailure(f"сеть: {e}") from e

            elapsed = time.monotonic() - started
            if resp.status_code == httpx.codes.OK:
                log.info(
                    "перевод: %s предложений, %s символов, %s→%s, глоссарий нет, %.2f с",
                    len(batch),
                    billed_chars(batch),
                    source,
                    TARGET_LANG,
                    elapsed,
                )
                return self._parse(resp, len(batch))

            detail = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if resp.status_code not in RETRIABLE_STATUSES:
                raise TranslateFailure(detail)
            last = detail
            log.warning("перевод, попытка %s: %s", attempt + 1, detail)
            await self._respect_retry_after(resp)

        raise TranslateFailure(f"провайдер не ответил после {self._retries + 1} попыток: {last}")

    def _backoff(self, attempt: int) -> float:
        """Паузы растут: 429 означает «слишком часто», а не «попробуй сразу»."""
        return 5.0 * attempt * attempt

    async def _respect_retry_after(self, resp: httpx.Response) -> None:
        """Провайдер сам сказал, сколько ждать — спорить с ним незачем."""
        raw = resp.headers.get("retry-after")
        if not raw:
            return
        try:
            delay = float(raw)
        except ValueError:
            return
        await asyncio.sleep(max(0.0, min(delay, 60.0)))

    @staticmethod
    def _parse(resp: httpx.Response, expected: int) -> list[str]:
        try:
            payload = resp.json()
        except ValueError as e:
            raise TranslateFailure(f"ответ не разобрался как JSON: {e}") from e

        items = payload.get("translations")
        if not isinstance(items, list) or len(items) != expected:
            got = len(items) if isinstance(items, list) else "нет поля translations"
            raise TranslateFailure(f"в ответе {got} переводов вместо {expected}")
        return [str(item.get("text", "")) for item in items]

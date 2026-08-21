"""Синтез русской речи через Yandex SpeechKit.

Озвучивается **перевод**, а не оригинал. Это осознанно и следует из того, зачем
вообще нужна читалка: оригинал разбирают по словам, а перевод слушают, когда
глаза устали. Синтезировать китайский или английский — задача другого сервиса
и другого читателя.

Решения, определяющие код:

* **единица синтеза — предложение.** Та же, что у перевода, и это не совпадение:
  у предложения уже есть устойчивый ключ (глава плюс `idx`), готовый текст и
  готовое место в интерфейсе. Глава целиком дала бы один файл на десять минут,
  который нельзя перемотать к нужной фразе и нельзя досинтезировать после
  отказа на середине;
* **mp3, а не oggopus.** Opus не играет в Safari на iPhone, а телефон здесь
  основной сценарий;
* **лимит провайдера — 5000 символов на запрос**, и предложение в него
  укладывается с запасом. Резать длинное предложение мы не будем: два mp3
  подряд склеиваются со щелчком, а само такое предложение — признак того, что
  резчик ошибся, и это надо чинить там.

Ключ живёт только на сервере. Роль нужна своя — `ai.speechkit-tts.user`;
`ai.translate.user` синтез не откроет, и это самая частая причина 403 здесь.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import settings
from app.domain import ErrorKind

log = logging.getLogger(__name__)

API_URL = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"

SPEECH_LANG = "ru-RU"

#: MIME по формату — им отдаётся файл наружу и с ним же он кэшируется.
CONTENT_TYPES = {
    "mp3": "audio/mpeg",
    "oggopus": "audio/ogg",
    "lpcm": "audio/x-pcm",
}

# Те же коды, что и у переводчика: перегрузка и таймауты на той стороне.
RETRIABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


class SpeechFailure(Exception):
    """Синтез не удался. Глава при этом читается — не получилось только слушать."""

    kind = ErrorKind.SPEECH_FAILED

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class SpeechResult:
    """Готовое аудио и то, за что заплачено."""

    audio: bytes
    content_type: str
    chars_sent: int


class Synthesizer(Protocol):
    """Смена провайдера должна стоить один файл — как и у переводчика."""

    voice: str
    content_type: str
    #: Всё, от чего зависит звучание, одной строкой. Входит в ключ кэша: смена
    #: голоса или скорости обязана дать новый файл, а не отдать старый.
    signature: str

    async def synthesize(self, text: str) -> SpeechResult: ...


class YandexSpeech:
    """Клиент SpeechKit v1. Один экземпляр на приложение."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        folder_id: str | None = None,
        voice: str | None = None,
        emotion: str | None = None,
        speed: float | None = None,
        audio_format: str | None = None,
        timeout: float | None = None,
        retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else settings.speech_api_key
        self._folder_id = folder_id if folder_id is not None else settings.yc_folder_id
        self.voice = voice or settings.speech_voice
        self._emotion = settings.speech_emotion if emotion is None else emotion
        self._speed = speed or settings.speech_speed
        self._format = audio_format or settings.speech_format
        self._timeout = timeout or settings.speech_timeout_seconds
        self._retries = retries
        self._client = client
        self._own_client = client is None

    @property
    def content_type(self) -> str:
        return CONTENT_TYPES.get(self._format, "application/octet-stream")

    @property
    def signature(self) -> str:
        return f"yandex|{self.voice}|{self._emotion}|{self._speed}|{self._format}"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._client is not None and self._own_client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> YandexSpeech:
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.close()

    async def synthesize(self, text: str) -> SpeechResult:
        """Озвучить один кусок текста. Бросает SpeechFailure с одной причиной."""
        text = text.strip()
        if not text:
            raise SpeechFailure("озвучивать нечего: пустой текст")
        if not self._api_key or not self._folder_id:
            raise SpeechFailure("не задан ключ или каталог SpeechKit")

        limit = settings.speech_max_chars_per_request
        if len(text) > limit:
            raise SpeechFailure(f"текст длиннее лимита провайдера: {len(text)} > {limit}")

        # form-urlencoded, а не JSON: v1 принимает только его.
        data = {
            "folderId": self._folder_id,
            "text": text,
            "lang": SPEECH_LANG,
            "voice": self.voice,
            "speed": str(self._speed),
            "format": self._format,
        }
        if self._emotion:
            data["emotion"] = self._emotion
        headers = {"Authorization": f"Api-Key {self._api_key}"}

        audio = await self._post(data, headers, len(text))
        return SpeechResult(audio=audio, content_type=self.content_type, chars_sent=len(text))

    async def _post(self, data: dict[str, str], headers: dict[str, str], chars: int) -> bytes:
        client = await self._get_client()

        last = ""
        for attempt in range(self._retries + 1):
            if attempt:
                await asyncio.sleep(self._backoff(attempt))
            started = time.monotonic()
            try:
                resp = await client.post(API_URL, data=data, headers=headers)
            except httpx.TimeoutException as e:
                last = f"таймаут: {e}"
                log.warning("синтез, попытка %s: %s", attempt + 1, last)
                continue
            except httpx.HTTPError as e:
                raise SpeechFailure(f"сеть: {e}") from e

            if resp.status_code == httpx.codes.OK:
                audio = resp.content
                if not audio:
                    raise SpeechFailure("провайдер вернул пустой файл")
                log.info(
                    "синтез: %s символов, голос %s, %s КБ, %.2f с",
                    chars,
                    self.voice,
                    len(audio) // 1024,
                    time.monotonic() - started,
                )
                return audio

            detail = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if resp.status_code == httpx.codes.FORBIDDEN:
                # Самая частая причина: у сервисного аккаунта есть
                # ai.translate.user, но нет ai.speechkit-tts.user.
                raise SpeechFailure(f"{detail} — проверьте роль ai.speechkit-tts.user")
            if resp.status_code not in RETRIABLE_STATUSES:
                raise SpeechFailure(detail)
            last = detail
            log.warning("синтез, попытка %s: %s", attempt + 1, detail)

        raise SpeechFailure(f"провайдер не ответил после {self._retries + 1} попыток: {last}")

    @staticmethod
    def _backoff(attempt: int) -> float:
        return 5.0 * attempt * attempt

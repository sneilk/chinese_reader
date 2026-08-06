"""Общий интерфейс загрузчиков и классификация отказов.

Классификация вынесена в чистую функцию нарочно: это единственная часть
загрузчика, которую можно проверить без браузера и сети, и именно она
определяет, что пользователь увидит вместо главы.
"""

from dataclasses import dataclass
from typing import Protocol

from app.domain import ErrorKind

# Заголовок страницы Cloudflare-челленджа. Проверяется вместе со статусом:
# по одному только 403 нельзя отличить челлендж от закрытого доступа.
_CHALLENGE_TITLES = ("just a moment", "attention required", "checking your browser")


@dataclass(frozen=True)
class FetchResult:
    """Что вернул загрузчик. `url` — финальный, после всех редиректов."""

    url: str
    status: int
    html: str
    title: str


class FetchFailure(Exception):
    """Отказ загрузки с причиной, пригодной для показа пользователю."""

    def __init__(self, kind: ErrorKind, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}" if detail else str(kind))
        self.kind = kind
        self.detail = detail


def classify(status: int, title: str, headers: dict[str, str] | None = None) -> ErrorKind | None:
    """Определить причину отказа. `None` означает, что страница годная.

    Порядок проверок важен: челлендж отдаётся с 403, поэтому распознаём его
    раньше, чем трактуем 403 как отказ в доступе.
    """
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    low_title = (title or "").strip().lower()

    if "cf-mitigated" in headers and headers["cf-mitigated"] == "challenge":
        return ErrorKind.CHALLENGE
    if any(low_title.startswith(t) for t in _CHALLENGE_TITLES):
        return ErrorKind.CHALLENGE
    if status == 404:
        return ErrorKind.NOT_FOUND
    if status in (401, 403):
        # Не челлендж и не 404 — для нас это неотличимо от «страницы нет».
        return ErrorKind.NOT_FOUND
    if status >= 400:
        return ErrorKind.FETCH_TIMEOUT if status in (408, 504) else ErrorKind.ADAPTER_ERROR
    return None


class Fetcher(Protocol):
    """Смена способа загрузки должна стоить один файл, RFC §3."""

    async def get(self, url: str) -> FetchResult: ...

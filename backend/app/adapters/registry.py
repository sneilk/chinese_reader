"""Выбор адаптера по URL.

Порядок важен: свои адаптеры идут первыми, generic — последним, потому что
он соглашается на любой адрес.
"""

from app.adapters.base import SiteAdapter
from app.adapters.generic import GenericAdapter
from app.adapters.novelarrow import NovelarrowAdapter
from app.adapters.shucheng import ShuchengAdapter

ADAPTERS: list[SiteAdapter] = [ShuchengAdapter(), NovelarrowAdapter(), GenericAdapter()]


def pick_adapter(url: str) -> SiteAdapter:
    for adapter in ADAPTERS:
        if adapter.matches(url):
            return adapter
    raise AssertionError("generic-адаптер обязан подходить под любой URL")

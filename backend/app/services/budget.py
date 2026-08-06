"""Мягкий потолок расходов на перевод (RFC §6).

Два лимита из конфига: на главу и на календарный месяц. Месяц календарный, а
не скользящий, потому что так тарифицирует провайдер (translation.md §3) —
иначе наш счёт и его счёт расходились бы в неудобную сторону.

Потолок мягкий: он останавливает **отправку**, а не чтение. Превысили — глава
остаётся на `segmented` и читается без переводов, ровно как при отказе
провайдера. Разница в том, что причина другая и чинится она не повтором,
поэтому `error_kind` у неё свой (`budget_exceeded`).

Проверка идёт до отправки и по полному объёму батча: узнать постфактум, что
глава стоила вдвое больше лимита, — это не потолок, а отчёт.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import TranslationUsage

log = logging.getLogger(__name__)


class BudgetExceeded(Exception):
    """Лимит расходов не позволяет отправить этот объём."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC).replace(tzinfo=None)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def chars_this_month(session: Session, now: datetime | None = None) -> int:
    """Сколько символов уже отправлено в текущем календарном месяце."""
    total = session.scalar(
        select(func.coalesce(func.sum(TranslationUsage.chars_sent), 0)).where(
            TranslationUsage.created_at >= month_start(now)
        )
    )
    return int(total or 0)


def check(session: Session, chars: int, *, spent_on_chapter: int = 0) -> None:
    """Проверить оба лимита до отправки. Бросает BudgetExceeded с числами."""
    per_chapter = settings.translate_max_chars_per_chapter
    per_month = settings.translate_max_chars_per_month

    chapter_total = spent_on_chapter + chars
    if per_chapter and chapter_total > per_chapter:
        raise BudgetExceeded(
            f"глава просит {chapter_total} символов при лимите {per_chapter} на главу"
        )

    if per_month:
        month_total = chars_this_month(session) + chars
        if month_total > per_month:
            raise BudgetExceeded(
                f"месяц просит {month_total} символов при лимите {per_month} на месяц"
            )


def record(
    session: Session,
    *,
    provider: str,
    direction: str,
    chars_sent: int,
    sentences: int,
    chapter_id: int | None = None,
) -> None:
    """Записать расход по подтверждённому ответу: там точное число символов."""
    session.add(
        TranslationUsage(
            provider=provider,
            direction=direction,
            chars_sent=chars_sent,
            sentences=sentences,
            chapter_id=chapter_id,
        )
    )
    session.commit()
    log.info("расход: %s символов, %s предложений, глава %s", chars_sent, sentences, chapter_id)

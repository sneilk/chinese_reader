"""Конвейер загрузки главы и статусная модель.

Порядок шагов и смысл статусов — RFC §4:

    fetching → [загрузка] → [адаптер] → [канон] → [предложения] → [токены]
             → segmented  ← ГЛАВА УЖЕ ЧИТАЕМА
             → translating → ready

Главное свойство, ради которого статусов пять, а не два: **отказ переводчика
не отменяет главу**. Текст и токены уже в базе, поэтому глава остаётся на
`segmented` с `error_kind=translate_failed` и читается без переводов, а
повторный перевод — отдельное действие. Всё, что случилось раньше `segmented`,
означает `failed`: показывать нечего.

Конвейер запускается фоновой задачей и потому **не бросает исключений
наружу**. Ловить их там некому, а непойманное исключение оставило бы главу в
`fetching` навсегда — с точки зрения читателя это вечный спиннер, худший из
возможных отказов.

Про async и синхронную сессию: загрузчик и переводчик асинхронные, SQLite —
нет. Внутри одной фоновой задачи это честно: обращения к базе короткие и
идут между await'ами, а тащить async-драйвер ради одного пользователя и
одного писателя (RFC §10) не за чем.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import AdapterFailure
from app.adapters.registry import pick_adapter
from app.db.models import Chapter, Sentence
from app.domain import ChapterStatus, ErrorKind
from app.fetchers.base import Fetcher, FetchFailure
from app.lang.normalize import normalize
from app.lang.segment import Segmenter, tokens_to_json
from app.lang.sentences import split_sentences
from app.providers.translate import TranslateFailure, Translator

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _fail(session: Session, chapter: Chapter, kind: ErrorKind, detail: str) -> None:
    chapter.status = ChapterStatus.FAILED
    chapter.error_kind = kind
    chapter.error_detail = detail[:2000]
    session.commit()
    log.warning("глава %s: %s — %s", chapter.id, kind, detail[:200])


async def run_chapter_pipeline(
    session: Session,
    chapter: Chapter,
    *,
    fetcher: Fetcher,
    segmenter: Segmenter,
    translator: Translator | None = None,
) -> Chapter:
    """Провести главу через конвейер. Исключения наружу не выпускает."""
    try:
        await _fetch_and_segment(session, chapter, fetcher=fetcher, segmenter=segmenter)
    except FetchFailure as e:
        _fail(session, chapter, e.kind, e.detail)
        return chapter
    except AdapterFailure as e:
        _fail(session, chapter, e.kind, e.detail)
        return chapter
    except Exception as e:  # noqa: BLE001 — фоновой задаче падать некуда
        log.exception("глава %s: непредвиденная ошибка разбора", chapter.id)
        _fail(session, chapter, ErrorKind.ADAPTER_ERROR, f"{type(e).__name__}: {e}")
        return chapter

    if translator is not None:
        await translate_chapter(session, chapter, translator)
    return chapter


async def _fetch_and_segment(
    session: Session,
    chapter: Chapter,
    *,
    fetcher: Fetcher,
    segmenter: Segmenter,
) -> None:
    """Загрузить, разобрать, нарезать и сохранить. Доводит главу до segmented."""
    chapter.status = ChapterStatus.FETCHING
    chapter.error_kind = None
    chapter.error_detail = None
    session.commit()

    result = await fetcher.get(chapter.url)
    raw = pick_adapter(chapter.url).parse_chapter(result.html, result.url)

    # Канон — нормализованный текст: офсеты токенов и предложений считаются
    # по нему, поэтому нормализация обязана произойти до всего остального.
    canon = normalize(raw.paragraphs)
    spans = split_sentences(canon)
    tokens = segmenter.segment(canon)

    chapter.title = raw.title or chapter.title
    chapter.content = canon
    chapter.tokens_json = tokens_to_json(tokens)
    chapter.fetched_at = _now()

    # Переразбор той же главы не должен плодить предложения поверх старых.
    chapter.sentences.clear()
    session.flush()
    chapter.sentences.extend(
        Sentence(idx=s.idx, start_offset=s.start, end_offset=s.end) for s in spans
    )

    chapter.status = ChapterStatus.SEGMENTED
    session.commit()
    log.info(
        "глава %s: %s символов, %s предложений, %s токенов",
        chapter.id,
        len(canon),
        len(spans),
        len(tokens),
    )


async def translate_chapter(
    session: Session,
    chapter: Chapter,
    translator: Translator,
) -> Chapter:
    """Перевести непереведённые предложения главы.

    Годится и для первого прохода, и для кнопки «перевести ещё раз»: уже
    переведённые предложения не переотправляются, поэтому повтор после
    частичного отказа стоит только недостающих символов (RFC §4).
    """
    if chapter.content is None:
        log.warning("глава %s: переводить нечего, текста нет", chapter.id)
        return chapter

    pending = _pending_sentences(session, chapter)
    if not pending:
        chapter.status = ChapterStatus.READY
        chapter.error_kind = None
        session.commit()
        return chapter

    chapter.status = ChapterStatus.TRANSLATING
    session.commit()

    texts = [chapter.content[s.start_offset : s.end_offset] for s in pending]
    try:
        result = await translator.translate(texts)
    except TranslateFailure as e:
        # Глава остаётся читаемой: текст и токены на месте, нет только переводов.
        chapter.status = ChapterStatus.SEGMENTED
        chapter.error_kind = ErrorKind.TRANSLATE_FAILED
        chapter.error_detail = e.detail[:2000]
        session.commit()
        log.warning("глава %s: перевод не удался — %s", chapter.id, e.detail[:200])
        return chapter
    except Exception as e:  # noqa: BLE001 — тот же расчёт, что и выше
        log.exception("глава %s: непредвиденная ошибка перевода", chapter.id)
        chapter.status = ChapterStatus.SEGMENTED
        chapter.error_kind = ErrorKind.TRANSLATE_FAILED
        chapter.error_detail = f"{type(e).__name__}: {e}"[:2000]
        session.commit()
        return chapter

    translated_at = _now()
    for sentence, text in zip(pending, result.texts, strict=True):
        sentence.translation = text
        sentence.translated_at = translated_at

    chapter.chars_sent += result.chars_sent
    chapter.status = ChapterStatus.READY
    chapter.error_kind = None
    chapter.error_detail = None
    session.commit()
    log.info(
        "глава %s переведена: %s предложений, %s символов, %s запрос(ов)",
        chapter.id,
        len(pending),
        result.chars_sent,
        result.requests,
    )
    return chapter


def _pending_sentences(session: Session, chapter: Chapter) -> Sequence[Sentence]:
    return (
        session.execute(
            select(Sentence)
            .where(Sentence.chapter_id == chapter.id, Sentence.translation.is_(None))
            .order_by(Sentence.idx)
        )
        .scalars()
        .all()
    )

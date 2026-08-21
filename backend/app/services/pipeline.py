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

## Язык узнаётся из текста, а не из настройки

Все три языковые развилки конвейера — чем резать на токены, какими правилами
резать на предложения, с какого языка переводить — решаются одним значением,
и приходит оно от адаптера вместе с текстом. Глобального «режима» нет
намеренно: главы двух языков лежат в одной базе и читаются вперемешку, а
переключатель режимов пришлось бы держать в голове и вспоминать перед каждой
ссылкой.

## Обход книги — отдельный шаг, а не продолжение загрузки

Одна глава загружается сама по себе; книга обходится по ссылке «следующая
глава» (`walk_chapters`). Разница принципиальная: у первой операции есть
понятный конец, у второй его нет, поэтому у неё есть потолок, остановка на
первом же отказе и защита от кольца ссылок. Склеивать их в одну функцию
значило бы дать любой обычной загрузке шанс уйти на двадцать запросов.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import urljoin

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import AdapterFailure, ChapterRaw, SiteAdapter
from app.adapters.registry import pick_adapter
from app.config import settings
from app.db.models import Chapter, Sentence
from app.domain import ChapterStatus, ErrorKind, Language
from app.fetchers.base import Fetcher, FetchFailure
from app.lang import segment_en
from app.lang.normalize import normalize
from app.lang.segment import Segmenter, Token, tokens_to_json
from app.lang.sentences import split_sentences
from app.providers.translate import TARGET_LANG, TranslateFailure, Translator
from app.services import budget
from app.services.chapters import get_or_create_chapter

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

    raw = await fetch_pages(fetcher, chapter.url, pick_adapter(chapter.url))

    # Язык объявляет адаптер, а generic-фолбэк определяет по тексту. До этого
    # места он был догадкой по адресу — с этого становится фактом.
    apply_language(chapter, raw.lang)

    # Канон — нормализованный текст: офсеты токенов и предложений считаются
    # по нему, поэтому нормализация обязана произойти до всего остального.
    canon = normalize(raw.paragraphs)
    spans = split_sentences(canon, raw.lang)
    tokens = tokenize(canon, raw.lang, segmenter)

    chapter.title = raw.title or chapter.title
    chapter.content = canon
    chapter.tokens_json = tokens_to_json(tokens)
    chapter.next_chapter_url = raw.next_chapter_url
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
        "глава %s (%s): %s символов, %s предложений, %s токенов",
        chapter.id,
        chapter.lang,
        len(canon),
        len(spans),
        len(tokens),
    )


def tokenize(canon: str, lang: Language, segmenter: Segmenter) -> list[Token]:
    """Разрезать текст на токены по правилам его языка.

    Китайскому нужен jieba со словарём — дорогой объект, живущий на всё
    приложение. Английскому не нужно ничего: границы слов уже проставлены
    пробелами, и вся работа — регулярка. Отсюда разная форма аргументов:
    сегментатор приходит снаружи, английский токенизатор — просто функция.
    """
    if lang is Language.EN:
        return segment_en.segment(canon)
    return segmenter.segment(canon)


def apply_language(chapter: Chapter, lang: Language) -> None:
    """Проставить главе язык из разбора и подтянуть за ней книгу.

    Книге язык правим только пока её главы одного языка: смешанная книга —
    это не книга, а совпавший префикс адреса, и молча переписывать ей язык
    по последней загруженной главе значило бы менять его туда-сюда.
    """
    chapter.lang = lang
    document = chapter.document
    if document is None:
        return
    siblings = {c.lang for c in document.chapters if c.id != chapter.id and c.content is not None}
    if not siblings or siblings == {lang}:
        document.lang = lang


async def fetch_pages(fetcher: Fetcher, url: str, adapter: SiteAdapter) -> ChapterRaw:
    """Загрузить главу целиком, склеив страницы, если адаптер их указывает.

    Склейка идёт **до** нормализации и сегментации (RFC §4): иначе офсеты и
    границы предложений считались бы по куску, а не по главе, и разъехались бы
    ровно на второй странице.

    Потолок в 20 страниц — жёсткий, и при его превышении глава падает с
    `adapter_error`. Молчаливая обрезка была бы хуже отказа: читатель получил
    бы главу без конца и никакого признака, что чего-то не хватает.

    Пауза между страницами отдельно здесь не нужна: её держит сам загрузчик
    (§1.3 концепции), одна на все свои запросы.
    """
    pages: list[ChapterRaw] = []
    visited: set[str] = set()
    next_url: str | None = url

    while next_url is not None:
        if next_url in visited:
            # Кольцо «следующая → предыдущая» встречается на живых сайтах;
            # без этой проверки оно просто упёрлось бы в потолок страниц.
            log.warning("глава %s: страница %s уже загружалась, останавливаюсь", url, next_url)
            break
        if len(pages) >= settings.max_pages_per_chapter:
            raise AdapterFailure(
                ErrorKind.ADAPTER_ERROR,
                f"страниц больше {settings.max_pages_per_chapter}: "
                f"это похоже на бесконечную пагинацию, а не на главу",
            )

        visited.add(next_url)
        result = await fetcher.get(next_url)
        page = adapter.parse_chapter(result.html, result.url)

        # Адаптер вправе вернуть относительную ссылку — считаем её от того
        # адреса, на котором в итоге оказались, а не от исходного. Обе ссылки
        # вперёд разрешаются одинаково, но пагинацию мы проходим сами, а
        # адрес следующей главы сохраняем и отдаём наружу.
        if page.next_chapter_url:
            page = replace(
                page, next_chapter_url=urljoin(result.url, page.next_chapter_url)
            )
        pages.append(page)

        next_url = urljoin(result.url, page.next_url) if page.next_url else None

    if len(pages) > 1:
        log.info("глава %s склеена из %s страниц", url, len(pages))

    first = pages[0]
    if len(pages) == 1:
        return first
    return ChapterRaw(
        title=first.title,
        paragraphs=[p for page in pages for p in page.paragraphs],
        lang=first.lang,
        # Ссылка на следующую главу берётся с последней страницы: на первой
        # её обычно нет вовсе, а если есть — она про ту же главу.
        next_chapter_url=pages[-1].next_chapter_url,
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

    texts = [chapter.content[s.start_offset : s.end_offset] for s in pending]
    try:
        # До отправки: узнать постфактум, что глава стоила вдвое больше
        # лимита, — это не потолок, а отчёт.
        budget.check(session, sum(len(t) for t in texts), spent_on_chapter=chapter.chars_sent)
    except budget.BudgetExceeded as e:
        chapter.status = ChapterStatus.SEGMENTED
        chapter.error_kind = ErrorKind.BUDGET_EXCEEDED
        chapter.error_detail = e.detail
        session.commit()
        log.warning("глава %s: потолок расходов — %s", chapter.id, e.detail)
        return chapter

    chapter.status = ChapterStatus.TRANSLATING
    session.commit()

    source = Language(chapter.lang)
    try:
        result = await translator.translate(texts, source=source)
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

    budget.record(
        session,
        provider="yandex",
        direction=f"{source}-{TARGET_LANG}",
        chars_sent=result.chars_sent,
        sentences=len(pending),
        chapter_id=chapter.id,
    )
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


async def walk_chapters(
    session: Session,
    chapter: Chapter,
    *,
    fetcher: Fetcher,
    segmenter: Segmenter,
    translator: Translator | None = None,
    limit: int,
) -> list[Chapter]:
    """Пройти книгу вперёд по ссылкам «следующая глава». Возвращает загруженное.

    Оглавления у novelarrow из разметки не достать (sources.md §2), поэтому
    другого входа в книгу нет. Отсюда три правила, и каждое — про то, чтобы
    обход закончился.

    **Потолок жёсткий.** Книга-образец — 550 глав; обход без предела означал бы
    полтысячи запросов к сайту с одного нажатия и счёт за перевод книги целиком.

    **Первый отказ останавливает.** Челлендж или пропавшая глава посреди книги —
    это повод разобраться, а не повод продолжить по инерции: следующие двадцать
    запросов почти наверняка получат то же самое.

    **Уже загруженная глава не перезагружается, но и не заканчивает обход.**
    Дочитав до места, где остановились в прошлый раз, читатель нажмёт «ещё
    десять» — и должен получить десять новых, а не «всё уже есть».
    """
    loaded: list[Chapter] = []
    current = chapter
    visited = {chapter.url}

    for _ in range(max(0, limit)):
        url = current.next_chapter_url
        if not url:
            log.info("обход книги: у главы %s нет ссылки вперёд", current.id)
            break
        if url in visited:
            log.warning("обход книги: %s уже встречался, останавливаюсь", url)
            break
        visited.add(url)

        nxt, created = get_or_create_chapter(session, url)
        if not created and nxt.status != ChapterStatus.FAILED:
            # Эта глава уже есть — перешагиваем через неё, не трогая сайт.
            current = nxt
            continue

        await run_chapter_pipeline(
            session, nxt, fetcher=fetcher, segmenter=segmenter, translator=translator
        )
        if nxt.status == ChapterStatus.FAILED:
            log.warning("обход книги оборван на %s: %s", url, nxt.error_kind)
            break

        loaded.append(nxt)
        current = nxt

    log.info("обход книги от главы %s: загружено %s", chapter.id, len(loaded))
    return loaded


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

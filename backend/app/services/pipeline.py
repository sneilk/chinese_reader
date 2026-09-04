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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
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
from app.services import budget, walks
from app.services.books import chain_tail
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


def recover_interrupted(session: Session) -> tuple[int, int]:
    """Починить главы, чью загрузку оборвал перезапуск. Возвращает (сколько, сколько).

    Фоновая задача живёт в процессе и перезапуск не переживает, а перезапуск —
    не редкость: им заканчивается каждая выкладка. Оборванная на середине глава
    остаётся в `fetching` или `translating`, то есть в состоянии «работа идёт»,
    хотя работать уже некому.

    Это тот самый вечный спиннер, ради которого конвейер ловит все исключения
    (см. шапку модуля), — только приходящий с другой стороны. И выхода из него
    у читателя нет: `POST /api/chapters` перезапускает главу в `failed`, а
    застрявшую в `fetching` считает уже загружаемой и не трогает.

    Разбор по состояниям здесь тот же, что и везде: где проходит граница
    читаемости, там и разница. До `segmented` показывать нечего — глава уходит
    в `failed`, откуда её вернёт обычный повтор. После — текст и разметка уже в
    базе, поэтому глава остаётся читаемой без переводов, а дозалить их можно
    кнопкой.
    """
    stale = (
        session.execute(
            select(Chapter).where(
                Chapter.status.in_((ChapterStatus.FETCHING, ChapterStatus.TRANSLATING))
            )
        )
        .scalars()
        .all()
    )
    if not stale:
        return 0, 0

    failed = readable = 0
    for chapter in stale:
        chapter.error_kind = ErrorKind.INTERRUPTED
        chapter.error_detail = "загрузку оборвал перезапуск сервиса"
        if chapter.status == ChapterStatus.FETCHING:
            chapter.status = ChapterStatus.FAILED
            failed += 1
        else:
            chapter.status = ChapterStatus.SEGMENTED
            readable += 1

    session.commit()
    log.warning(
        "после перезапуска: %s глав(ы) помечены прерванными, %s остались читаемыми",
        failed,
        readable,
    )
    return failed, readable


@dataclass
class WalkResult:
    """Чем кончился обход книги.

    Одного списка загруженного мало: «загружено ноль» одинаково выглядит и
    когда книга дочитана до конца, и когда первая же глава упёрлась в
    челлендж. Разница здесь принципиальная — во втором случае есть что чинить.
    """

    loaded: list[Chapter] = field(default_factory=list)
    #: `error_kind` главы, оборвавшей обход. `None` — остановка штатная:
    #: ссылки вперёд не стало, встретилось кольцо или упёрлись в потолок.
    stopped_by: str | None = None

    def __len__(self) -> int:
        return len(self.loaded)


@dataclass(frozen=True)
class Relinked:
    """Чем кончился поход за ссылкой вперёд.

    Различать «сайт не дал ссылки» и «до сайта не дошли» приходится потому,
    что выглядят они одинаково — ссылки нет, — а означают противоположное:
    первое это конец книги, второе неисправленный отказ. Выгрузка книги на
    этом и ошибалась: получив челлендж, она сообщала «книга кончилась».
    """

    url: str | None = None
    #: Причина, по которой спросить не вышло. `None` — спросили и получили ответ.
    failed_with: str | None = None


async def relink_chapter(session: Session, chapter: Chapter, fetcher: Fetcher) -> Relinked:
    """Заново узнать у сайта, куда ведёт глава.

    Нужно ровно для одного случая, зато неизбежного: глава загружена тогда,
    когда её адаптер ссылку вперёд не читал, и `next_chapter_url` у неё пуст
    навсегда. Таких глав в базе целая китайская половина, и по ним книга не
    едет никуда.

    Перезагружать главу целиком ради одного поля нельзя: конвейер пересобирает
    предложения (`chapter.sentences.clear()`), а вместе с ними уезжают
    переводы — то есть деньги, уже потраченные на эту главу. Поэтому здесь
    страница разбирается, но записывается из неё **только ссылка**.

    Отказ не портит главу: текст на месте, читать её можно, а «куда дальше»
    просто остаётся неизвестным.
    """
    try:
        result = await fetcher.get(chapter.url)
        raw = pick_adapter(chapter.url).parse_chapter(result.html, result.url)
    except (FetchFailure, AdapterFailure) as e:
        log.warning("глава %s: ссылку вперёд узнать не удалось — %s", chapter.id, e.kind)
        return Relinked(failed_with=e.kind)
    except Exception:  # noqa: BLE001 — обход не должен падать из-за одной ссылки
        log.exception("глава %s: непредвиденная ошибка при поиске ссылки вперёд", chapter.id)
        return Relinked(failed_with=ErrorKind.ADAPTER_ERROR)

    if not raw.next_chapter_url:
        log.info("глава %s: сайт ссылки вперёд не даёт, книга кончилась", chapter.id)
        return Relinked()

    chapter.next_chapter_url = urljoin(result.url, raw.next_chapter_url)
    session.commit()
    log.info("глава %s: ссылка вперёд восстановлена — %s", chapter.id, chapter.next_chapter_url)
    return Relinked(url=chapter.next_chapter_url)


async def walk_chapters(
    session: Session,
    chapter: Chapter,
    *,
    fetcher: Fetcher,
    segmenter: Segmenter,
    translator: Translator | None = None,
    limit: int,
    on_chapter: Callable[[Chapter], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> WalkResult:
    """Пройти книгу вперёд по ссылкам «следующая глава». Возвращает загруженное.

    Оглавления у novelarrow из разметки не достать (sources.md §2), поэтому
    другого входа в книгу нет. Отсюда три правила, и каждое — про то, чтобы
    обход закончился.

    **Потолок жёсткий.** Книга-образец — 550 глав; обход без предела означал бы
    полтысячи запросов к сайту с одного нажатия и счёт за перевод книги целиком.

    **Первый отказ останавливает.** Челлендж или пропавшая глава посреди книги —
    это повод разобраться, а не повод продолжить по инерции: следующие двадцать
    запросов почти наверняка получат то же самое.

    **Уже загруженная глава не перезагружается, но и не тратит потолок.**
    Дочитав до места, где остановились в прошлый раз, читатель нажмёт «ещё
    десять» — и должен получить десять новых, а не «всё уже есть». Поэтому
    `limit` считает **загруженные** главы, а не шаги: перешагивание через
    десять уже имеющихся не должно съедать весь запрос.

    Шаги при этом тоже ограничены, и отдельно: цепочка из тысяч уже известных
    глав не повод ходить по ней вечно, а `limit` от неё не убывает и сам обход
    не остановит.

    `on_chapter` зовётся на каждую загруженную главу. Нужен он выгрузке книги
    целиком: та идёт час, и прогресс, приезжающий одним числом в конце, — это
    не прогресс.

    `should_stop` спрашивается между главами. Останавливаться посреди главы
    незачем — она докачается за две секунды и ляжет в базу целой, — а вот
    между ними это единственный способ уйти: обход живёт внутри фоновой
    задачи, а её uvicorn при остановке сервиса **ждёт**.
    """
    result = WalkResult()
    current = chapter
    visited = {chapter.url}
    wanted = max(0, limit)
    # Потолок шагов, а не загрузок: он существует только затем, чтобы обход
    # кончился, даже если цепочка ссылок оказалась кольцом длиннее проверки на
    # повтор. К «сколько глав просили» отношения не имеет.
    steps_left = wanted + settings.max_chapters_per_book

    while len(result.loaded) < wanted and steps_left > 0:
        steps_left -= 1
        if walks.shutting_down() or (should_stop is not None and should_stop()):
            log.info("обход книги от главы %s прекращён по просьбе", chapter.id)
            break

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
            result.stopped_by = nxt.error_kind
            break

        result.loaded.append(nxt)
        if on_chapter is not None:
            on_chapter(nxt)
        current = nxt

    log.info("обход книги от главы %s: загружено %s", chapter.id, len(result.loaded))
    return result


async def walk_book(
    session: Session,
    document_id: int,
    *,
    fetcher: Fetcher,
    segmenter: Segmenter,
    translator: Translator | None = None,
    limit: int,
) -> WalkResult:
    """Выгрузить книгу вперёд от конца известной цепочки.

    От обхода «ещё N глав» отличается не размером потолка, а тем, что читатель
    не показывает, откуда идти: книга знает это сама. Началом берётся конец
    цепочки — самая дальняя глава с известным местом (`books.chain_tail`).

    Отдельный случай — та самая глава без ссылки вперёд. У неё два разных
    смысла, и различить их из базы нельзя: либо книга кончилась, либо главу
    загрузили тогда, когда ссылки не читались вовсе. Поэтому у сайта
    спрашивается заново — одной страницей, без перезагрузки текста.
    """
    tail = chain_tail(session, document_id)
    if tail is None:
        log.info("книга %s пуста, обходить нечего", document_id)
        return WalkResult()

    if tail.content is None:
        # Ни одна глава книги не загрузилась. Идти вперёд не от чего, и это не
        # «книга кончилась», а неисправленный отказ первой главы: молчание
        # здесь выглядело бы как «кнопка ничего не делает».
        log.warning("книга %s: ни одной загруженной главы, обход невозможен", document_id)
        return WalkResult(stopped_by=tail.error_kind or ErrorKind.EMPTY_EXTRACT)

    if not tail.next_chapter_url:
        asked = await relink_chapter(session, tail, fetcher)
        if asked.failed_with is not None:
            # До сайта не дошли — значит про конец книги мы ничего не узнали.
            # Промолчать здесь означало бы сказать «книга кончилась» вместо
            # «сайт просит проверку», то есть посоветовать не делать ничего.
            return WalkResult(stopped_by=asked.failed_with)

    return await walk_chapters(
        session,
        tail,
        fetcher=fetcher,
        segmenter=segmenter,
        translator=translator,
        limit=limit,
        on_chapter=lambda _chapter: walks.note_loaded(document_id),
        should_stop=lambda: walks.should_stop(document_id),
    )


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

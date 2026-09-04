"""Состояние сервиса: `GET /api/diagnostics` (T2.10).

Отвечает на один вопрос — «что чинить». Сообщения по `error_kind` объясняют
отказ конкретной главы, но половина настоящих поломок ими не видна: словарь
не импортирован, ключ переводчика не задан, профиль браузера потерян после
пересоздания машины. Всё это выглядит одинаково — «не работает», — а чинится
по-разному.

Секретов здесь нет и быть не должно: только «ключ задан» или «не задан», без
самого ключа и без адресов.

Две проверки из ряда выбиваются, и обе — потому что их ответ нельзя прочитать,
можно только попробовать: синтез речи и проверка сайта в живом браузере.
Первая тратит деньги, вторая — время и окно на экране, поэтому обе запускаются
нажатием, а не открытием экрана.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from app.api.deps import FetcherDep, SessionDep, SynthesizerDep
from app.api.schemas import BrowserCheckIn, BrowserCheckOut, DiagnosticsOut, SpeechCheckOut
from app.config import settings
from app.db.models import Chapter, DictEntry, Sentence, UserWord
from app.domain import ErrorKind
from app.providers.speech import SpeechFailure
from app.services.budget import chars_this_month, speech_chars_this_month
from app.services.speech import cache_size_bytes

log = logging.getLogger(__name__)

router = APIRouter(tags=["diagnostics"])


def _count(session: SessionDep, model) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


@router.get("/diagnostics", response_model=DiagnosticsOut)
def read_diagnostics(session: SessionDep) -> DiagnosticsOut:
    by_source = dict(
        session.execute(select(DictEntry.source, func.count()).group_by(DictEntry.source)).all()
    )

    userdict = settings.userdict_path
    db_path = settings.db_path

    return DiagnosticsOut(
        version="0.1.0",
        schema_revision=_schema_revision(session),
        db_size_bytes=db_path.stat().st_size if db_path.exists() else 0,
        chapters=_count(session, Chapter),
        sentences=_count(session, Sentence),
        user_words=_count(session, UserWord),
        dict_entries=sum(by_source.values()),
        dict_sources={str(k): int(v) for k, v in by_source.items()},
        userdict_words=sum(1 for _ in userdict.open(encoding="utf-8")) if userdict.exists() else 0,
        translator_configured=bool(settings.yc_translate_api_key and settings.yc_folder_id),
        chars_this_month=chars_this_month(session),
        month_limit=settings.translate_max_chars_per_month,
        speech_configured=bool(settings.speech_api_key and settings.yc_folder_id),
        speech_voice=settings.speech_voice,
        speech_chars_this_month=speech_chars_this_month(session),
        speech_month_limit=settings.speech_max_chars_per_month,
        tts_cache_bytes=cache_size_bytes(),
        browser_profile_exists=settings.browser_profile_dir.exists(),
        browser_headless=settings.browser_headless,
    )


@router.post("/diagnostics/speech-check", response_model=SpeechCheckOut)
async def check_speech(synthesizer: SynthesizerDep) -> SpeechCheckOut:
    """Озвучить одно слово и сказать, что из этого вышло.

    Единственное на этом экране, чего нельзя узнать, не попробовав. «Ключ
    задан» проверяется чтением настроек и горит зелёным даже тогда, когда
    работать не будет: ключ может быть от того же сервисного аккаунта, что и у
    переводчика, а роль `ai.speechkit-tts.user` ему не выдана — и синтез
    ответит 403 при первом же нажатии «Слушать».

    Поэтому это POST, а не GET: проверка тратит деньги — восемь символов по
    тарифу синтеза — и должна происходить по решению, а не при открытии экрана.
    """
    if synthesizer is None:
        return SpeechCheckOut(ok=False, kind=ErrorKind.SPEECH_FAILED, detail="ключ не задан")

    try:
        result = await synthesizer.synthesize("проверка")
    except SpeechFailure as e:
        return SpeechCheckOut(ok=False, kind=ErrorKind.SPEECH_FAILED, detail=e.detail[:300])

    return SpeechCheckOut(
        ok=True,
        detail=f"голос {synthesizer.voice}, {len(result.audio) // 1024} КБ",
    )


@router.post("/diagnostics/browser-check", response_model=BrowserCheckOut)
async def check_browser(payload: BrowserCheckIn, fetcher: FetcherDep) -> BrowserCheckOut:
    """Открыть страницу в окне браузера и подождать, пока проверку пройдут руками.

    Обычно челлендж проходится сам, и тогда ответ приходит через пару секунд —
    ждать до конца окна не приходится. Но «обычно» не значит «всегда»: если
    Cloudflare показал капчу, нажать на неё может только человек, а до этой
    ручки такого случая просто не существовало. Глава уходила в `failed` с
    советом «попробуйте через минуту», который в этом случае не помогает
    никогда.

    Окно живёт там же, где браузер: на разработческой машине — на экране, на
    ВМ — на Xvfb, и смотрят его через VNC. Чтобы увидеть проверку и без VNC,
    рядом отдаётся снимок экрана.

    Ответ ждёт столько, сколько попросили, и это осознанно: запускать ещё одну
    фоновую задачу с опросом статуса ради проверки, которая обычно занимает
    пять секунд, дороже самой проверки.
    """
    if not hasattr(fetcher, "open_for_check"):
        # Так выглядит подменённый загрузчик в тестах и любой будущий,
        # работающий без браузера: окна у него нет, и открывать нечего.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, ErrorKind.ADAPTER_ERROR)

    result = await fetcher.open_for_check(
        payload.url,
        seconds=payload.seconds,
        screenshot_path=settings.browser_check_screenshot,
    )
    return BrowserCheckOut(
        ok=result.ok,
        kind=str(result.kind) if result.kind else None,
        status=result.status,
        title=result.title[:300],
        url=result.url,
        waited_seconds=round(result.waited_seconds, 1),
        visible=result.visible,
        screenshot=result.screenshot is not None,
    )


@router.get("/diagnostics/browser-check/screenshot")
def read_browser_screenshot() -> FileResponse:
    """Снимок экрана последней проверки. Единственный способ увидеть капчу без VNC."""
    path = settings.browser_check_screenshot
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, ErrorKind.NOT_FOUND)

    return FileResponse(
        path,
        media_type="image/png",
        # Снимок один и перезаписывается: закэшированный показывал бы прошлую
        # проверку вместо той, которую только что запустили.
        headers={"cache-control": "no-store"},
    )


def _schema_revision(session: SessionDep) -> str | None:
    """Версия схемы из таблицы alembic. `None`, если миграции не накатывались.

    Отсутствие таблицы — не ошибка, а диагноз: база создана в обход миграций.
    Падать здесь нельзя, ручку зовут как раз тогда, когда что-то не так.
    """
    from sqlalchemy import Column, MetaData, String, Table
    from sqlalchemy.exc import DatabaseError

    table = Table("alembic_version", MetaData(), Column("version_num", String))
    try:
        return session.scalar(select(func.max(table.c.version_num)))
    except DatabaseError:
        session.rollback()
        return None

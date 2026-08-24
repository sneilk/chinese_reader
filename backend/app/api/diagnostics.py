"""Состояние сервиса: `GET /api/diagnostics` (T2.10).

Отвечает на один вопрос — «что чинить». Сообщения по `error_kind` объясняют
отказ конкретной главы, но половина настоящих поломок ими не видна: словарь
не импортирован, ключ переводчика не задан, профиль браузера потерян после
пересоздания машины. Всё это выглядит одинаково — «не работает», — а чинится
по-разному.

Секретов здесь нет и быть не должно: только «ключ задан» или «не задан», без
самого ключа и без адресов.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import SessionDep, SynthesizerDep
from app.api.schemas import DiagnosticsOut, SpeechCheckOut
from app.config import settings
from app.db.models import Chapter, DictEntry, Sentence, UserWord
from app.domain import ErrorKind
from app.providers.speech import SpeechFailure
from app.services.budget import chars_this_month, speech_chars_this_month
from app.services.speech import cache_size_bytes

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

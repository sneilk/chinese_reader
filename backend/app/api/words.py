"""Ручки личного словаря (RFC §8).

Смысл этих четырёх ручек — критерий приёмки точки 2: «ни одной причины лезть
в SQLite руками». Поэтому здесь есть и правка своих полей, и удаление
ошибочной записи, а не только сохранение.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import OptionalSegmenterDep, SessionDep
from app.api.schemas import WordCreate, WordOut, WordsPage, WordUpdate
from app.config import settings
from app.db.models import UserWord
from app.domain import ErrorKind
from app.lang.segment import teach_word
from app.services.words import (
    ContextInput,
    WordError,
    delete_word,
    list_words,
    save_word,
    update_word,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["words"])


def _get_or_404(session: SessionDep, word_id: int) -> UserWord:
    word = session.get(UserWord, word_id)
    if word is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, ErrorKind.NOT_FOUND)
    return word


@router.post("/words", status_code=status.HTTP_201_CREATED, response_model=WordOut)
def create_word(
    payload: WordCreate,
    session: SessionDep,
    segmenter: OptionalSegmenterDep = None,
) -> WordOut:
    """Сохранить слово с контекстом и научить ему сегментатор (T2.7).

    Повторное сохранение того же слова добавляет контекст к существующей
    карточке: одно слово — одна запись, сколько бы раз оно ни встретилось.

    Сохранение — это и есть правка границ: читатель склеил `张仙姑` из двух
    кусков, и с этого момента слово режется целиком. В новеллах имя героя
    встречается сотнями раз, поэтому одна правка окупается сразу
    (segmentation.md §5).
    """
    context = None
    if payload.context is not None:
        context = ContextInput(
            sentence=payload.context.sentence,
            offset_start=payload.context.offset_start,
            offset_end=payload.context.offset_end,
            chapter_id=payload.context.chapter_id,
            sentence_id=payload.context.sentence_id,
        )

    try:
        word, _created = save_word(
            session,
            headword=payload.headword,
            lang=payload.lang,
            reading=payload.reading,
            user_translation=payload.user_translation,
            note=payload.note,
            context=context,
        )
    except WordError as e:
        # 422 числом, а не константой: имя HTTP_422_UNPROCESSABLE_ENTITY в
        # starlette объявлено устаревшим, а новое есть не во всех версиях.
        raise HTTPException(422, str(e)) from e

    # Учим только на первом сохранении: при повторном слово уже в userdict,
    # и дописывание плодило бы одинаковые строки в файле.
    if _created:
        teach_word(segmenter, settings.userdict_path, word.headword)

    return WordOut.of(word)


@router.get("/words", response_model=WordsPage)
def read_words(
    session: SessionDep,
    lang: str | None = Query(default=None, max_length=8),
    query: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> WordsPage:
    items, total = list_words(session, lang=lang, query=query, limit=limit, offset=offset)
    return WordsPage(items=[WordOut.of(w) for w in items], total=total)


@router.patch("/words/{word_id}", response_model=WordOut)
def patch_word(word_id: int, payload: WordUpdate, session: SessionDep) -> WordOut:
    word = _get_or_404(session, word_id)
    updated = update_word(
        session,
        word,
        reading=payload.reading,
        user_translation=payload.user_translation,
        note=payload.note,
    )
    return WordOut.of(updated)


@router.delete("/words/{word_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_word(word_id: int, session: SessionDep) -> None:
    delete_word(session, _get_or_404(session, word_id))

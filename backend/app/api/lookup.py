"""Ручка словаря: `GET /api/lookup` (RFC §8).

Отдаёт значения из локальных словарей. Сети здесь нет и не будет: словари
лежат в той же базе, поэтому карточка открывается без задержки и без интернета.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.api.schemas import CharGlossOut, DictEntryOut, LookupOut
from app.services.lookup import lookup

router = APIRouter(tags=["dictionary"])


@router.get("/lookup", response_model=LookupOut)
def read_lookup(
    session: SessionDep,
    word: str = Query(min_length=1, max_length=64),
    lang: str = Query(default="zh", max_length=8),
) -> LookupOut:
    result = lookup(session, word, lang)
    return LookupOut(
        word=result.word,
        found=result.found,
        approximate=result.approximate,
        entries=[
            DictEntryOut(
                headword=e.headword,
                traditional=e.traditional,
                reading=e.reading,
                senses=e.senses,
                source=e.source,
            )
            for e in result.entries
        ],
        chars=[CharGlossOut(char=c.char, reading=c.reading, senses=c.senses) for c in result.chars],
    )

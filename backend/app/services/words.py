"""Личный словарь: сохранение слов вместе с контекстом.

Одно слово — одна карточка, сколько бы раз читатель на него ни наткнулся.
Повторное сохранение добавляет новый контекст к существующей записи, а не
заводит дубль: иначе после главы с именем героя словарь превратился бы в
список из полусотни одинаковых строк.

Контекст хранит текст предложения копией (RFC §7). Это не денормализация ради
скорости, а условие того, чтобы карточка пережила удаление главы: офсеты по
исчезнувшему тексту не значат ничего.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Chapter, Context, Sentence, UserWord

log = logging.getLogger(__name__)

# Столько контекстов на слово хватает, чтобы увидеть разные употребления.
# Дальше они лишь копят одинаковые предложения из одной и той же главы.
MAX_CONTEXTS_PER_WORD = 20


@dataclass(frozen=True)
class ContextInput:
    sentence: str
    offset_start: int
    offset_end: int
    chapter_id: int | None = None
    sentence_id: int | None = None


class WordError(ValueError):
    """Слово нельзя сохранить: причина пригодна для показа пользователю."""


def _validate(headword: str, context: ContextInput | None) -> None:
    if not headword.strip():
        raise WordError("слово пустое")
    if context is None:
        return
    if context.offset_start < 0 or context.offset_end < context.offset_start:
        raise WordError("офсеты контекста не образуют отрезок")
    if context.offset_end > len(context.sentence):
        raise WordError("офсеты контекста выходят за предложение")

    # Офсеты обязаны резать предложение обратно в само слово. Без этой
    # проверки сдвиг на единицу сохраняется молча, а всплывает через месяц
    # в карточке, где подсвечено соседнее слово — и выглядит как ошибка
    # разметки главы, а не как испорченная запись.
    cut = context.sentence[context.offset_start : context.offset_end]
    if cut != headword.strip():
        raise WordError(f"офсеты режут {cut!r}, а слово — {headword!r}")


def _check_links(session: Session, context: ContextInput) -> None:
    """Проверить ссылки на главу и предложение до вставки.

    Иначе внешний ключ сработает уже внутри базы, и наружу уйдёт пятисотка с
    текстом SQLAlchemy вместо внятного отказа.
    """
    if context.chapter_id is not None and session.get(Chapter, context.chapter_id) is None:
        raise WordError(f"главы {context.chapter_id} нет")
    if context.sentence_id is not None and session.get(Sentence, context.sentence_id) is None:
        raise WordError(f"предложения {context.sentence_id} нет")


def save_word(
    session: Session,
    *,
    headword: str,
    lang: str = "zh",
    reading: str | None = None,
    user_translation: str | None = None,
    note: str | None = None,
    context: ContextInput | None = None,
) -> tuple[UserWord, bool]:
    """Сохранить слово. Второе значение — «завели сейчас», а не дополнили."""
    headword = headword.strip()
    _validate(headword, context)

    if context is not None:
        _check_links(session, context)

    word = session.scalars(
        select(UserWord).where(UserWord.lang == lang, UserWord.headword == headword)
    ).first()
    created = word is None

    if word is None:
        word = UserWord(lang=lang, headword=headword, reading=reading)
        session.add(word)
        session.flush()
    else:
        # Чтение и свои поля обновляем только если их прислали: пустое поле в
        # запросе означает «не трогай», а не «сотри».
        if reading and not word.reading:
            word.reading = reading

    if user_translation is not None:
        word.user_translation = user_translation
    if note is not None:
        word.note = note

    if context is not None:
        _add_context(session, word, context)

    session.commit()
    log.info("слово %s: %s", headword, "заведено" if created else "дополнено")
    return word, created


def _add_context(session: Session, word: UserWord, context: ContextInput) -> None:
    same = session.scalars(
        select(Context).where(
            Context.user_word_id == word.id,
            Context.sentence == context.sentence,
            Context.offset_start == context.offset_start,
        )
    ).first()
    if same is not None:
        # Тот же кусок того же предложения — второй раз он ничего не добавляет.
        return

    count = session.scalar(
        select(func.count()).select_from(Context).where(Context.user_word_id == word.id)
    )
    if count is not None and count >= MAX_CONTEXTS_PER_WORD:
        return

    session.add(
        Context(
            user_word_id=word.id,
            chapter_id=context.chapter_id,
            sentence_id=context.sentence_id,
            sentence=context.sentence,
            offset_start=context.offset_start,
            offset_end=context.offset_end,
        )
    )


def list_words(
    session: Session,
    *,
    lang: str | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[Sequence[UserWord], int]:
    """Страница словаря плюс общее число слов — для «показано N из M»."""
    where = []
    if lang:
        where.append(UserWord.lang == lang)
    if query:
        needle = f"%{query.strip()}%"
        where.append(UserWord.headword.like(needle) | UserWord.user_translation.like(needle))

    total = session.scalar(select(func.count()).select_from(UserWord).where(*where)) or 0
    rows = session.scalars(
        select(UserWord)
        .where(*where)
        .options(selectinload(UserWord.contexts))
        # Свежие сверху: только что сохранённое слово ищут чаще старого.
        .order_by(UserWord.added_at.desc(), UserWord.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return rows, total


def update_word(
    session: Session,
    word: UserWord,
    *,
    reading: str | None = None,
    user_translation: str | None = None,
    note: str | None = None,
) -> UserWord:
    """Правка своих полей. `None` означает «не трогать», пустая строка — «стереть»."""
    if reading is not None:
        word.reading = reading or None
    if user_translation is not None:
        word.user_translation = user_translation or None
    if note is not None:
        word.note = note or None
    session.commit()
    return word


def delete_word(session: Session, word: UserWord) -> None:
    session.delete(word)
    session.commit()

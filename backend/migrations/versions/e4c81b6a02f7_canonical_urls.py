"""Свести адреса глав и книг к каноническому виду.

Revision ID: e4c81b6a02f7
Revises: c5a70b41e8d2

Тождество главы — это `chapters.url`, и на нём держится всё: повторный
`POST /api/chapters` не идёт на сайт, обход книги не заводит дубль, книга
собирается по общему префиксу адреса. Пока сравнивались сырые строки, одна и
та же страница заводилась дважды от любой мелочи в написании ссылки —
`?restore=1` (novelarrow приписывает его сам), `www.` против голого хоста,
регистр, порт по умолчанию. Книга при этом раздваивалась вместе с главой, и
оглавление становилось неполным в обеих половинах.

Правила канонизации здесь **повторены**, а не позаимствованы из
`app.adapters.base`. Так и надо: миграция — снимок правил на свой момент, и
если завтра правило изменится, эта миграция обязана сделать то же, что делала
сегодня. Импорт кода приложения дал бы обратное.

## Что происходит с дублями

Из каждой группы выживает **самая богатая** — та, где больше переведённых
предложений. Это не эстетика: за перевод заплачено, и выбрасывать надо то, за
что не платили. При равенстве предпочитается глава с текстом, затем —
заведённая раньше.

Проигравшие удаляются вместе с предложениями. Слова из личного словаря при
этом не страдают: контексты хранят копию текста, а ссылки на главу
обнуляются — ровно затем такая копия и лежит (`db/models.py`, Context).

Каскады прописаны здесь явно, а не оставлены базе: у alembic своё соединение,
и полагаться на то, что в нём включён `PRAGMA foreign_keys`, нельзя.

Обратной миграции нет. Исходные адреса не сохраняются — восстановить из
канонического, какое именно написание было, невозможно.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import sqlalchemy as sa
from alembic import op

revision = "e4c81b6a02f7"
down_revision = "c5a70b41e8d2"
branch_labels = None
depends_on = None

# Сайты, у которых адрес главы — это путь, а строка запроса лишь состояние
# интерфейса. Для остальных запрос сохраняется: у незнакомого сайта в нём
# может лежать номер страницы, и склеив такие адреса, мы потеряли бы текст.
_QUERYLESS_HOSTS = ("51shucheng.net", "novelarrow.com")
_DEFAULT_PORTS = {"http": (80,), "https": (443,)}


def _canonical(url: str) -> str:
    parsed = urlsplit((url or "").strip())
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]

    netloc = host
    if parsed.port is not None and parsed.port not in _DEFAULT_PORTS.get(parsed.scheme.lower(), ()):
        netloc = f"{host}:{parsed.port}"

    keep_query = not any(host == h or host.endswith(f".{h}") for h in _QUERYLESS_HOSTS)
    return urlunsplit((
        parsed.scheme.lower(),
        netloc,
        parsed.path or "/",
        parsed.query if keep_query else "",
        "",
    ))


def _book_key(url: str) -> str:
    parsed = urlsplit(_canonical(url))
    path = parsed.path.rsplit("/", 1)[0]
    return f"{parsed.scheme}://{parsed.netloc}{path}/"


def _drop_chapters(conn, ids: list[int]) -> None:
    """Убрать главы вместе со всем, что на них ссылается."""
    if not ids:
        return
    params = {"ids": tuple(ids)}
    # Личный словарь переживает удаление главы — в этом весь смысл копии.
    conn.execute(
        sa.text(
            "UPDATE contexts SET chapter_id = NULL, sentence_id = NULL "
            "WHERE chapter_id IN :ids"
        ).bindparams(sa.bindparam("ids", expanding=True)),
        params,
    )
    for table in ("translation_usage", "speech_usage"):
        conn.execute(
            sa.text(
                f"UPDATE {table} SET chapter_id = NULL WHERE chapter_id IN :ids"
            ).bindparams(sa.bindparam("ids", expanding=True)),
            params,
        )
    for table in ("word_occurrences", "sentences"):
        conn.execute(
            sa.text(f"DELETE FROM {table} WHERE chapter_id IN :ids").bindparams(
                sa.bindparam("ids", expanding=True)
            ),
            params,
        )
    conn.execute(
        sa.text("DELETE FROM chapters WHERE id IN :ids").bindparams(
            sa.bindparam("ids", expanding=True)
        ),
        params,
    )


def upgrade() -> None:
    conn = op.get_bind()

    rows = conn.execute(
        sa.text(
            "SELECT c.id, c.url, c.content IS NOT NULL AS has_text, "
            "       (SELECT COUNT(*) FROM sentences s "
            "         WHERE s.chapter_id = c.id AND s.translation IS NOT NULL) AS translated "
            "  FROM chapters c"
        )
    ).all()

    groups: dict[str, list[tuple]] = {}
    for row in rows:
        groups.setdefault(_canonical(row.url), []).append(row)

    # Сначала решаем, кто выживает, потом убираем проигравших, и только затем
    # переименовываем. Порядок обязателен: `chapters.url` уникален, и если
    # переименовать выжившего раньше, он столкнётся с ещё не удалённым дублем,
    # который эту самую строку и занимает.
    doomed: list[int] = []
    keepers: list[tuple[int, str]] = []
    for canonical, members in groups.items():
        # Богаче — значит больше оплаченного перевода. Дальше: с текстом
        # важнее пустой, при полном равенстве — та, что заведена раньше.
        ranked = sorted(members, key=lambda r: (-r.translated, not r.has_text, r.id))
        doomed.extend(m.id for m in ranked[1:])
        keepers.append((ranked[0].id, canonical))

    _drop_chapters(conn, doomed)

    for chapter_id, canonical in keepers:
        conn.execute(
            sa.text("UPDATE chapters SET url = :url WHERE id = :id"),
            {"url": canonical, "id": chapter_id},
        )

    # Ссылка вперёд — тот же ключ: по ней ищут уже загруженную соседку.
    for chapter_id, next_url in conn.execute(
        sa.text("SELECT id, next_chapter_url FROM chapters WHERE next_chapter_url IS NOT NULL")
    ).all():
        conn.execute(
            sa.text("UPDATE chapters SET next_chapter_url = :url WHERE id = :id"),
            {"url": _canonical(next_url), "id": chapter_id},
        )

    # Книги: ключ пересчитывается, совпавшие сливаются в одну. Порядок тот же
    # и по той же причине — `documents.key` тоже уникален.
    survivors: dict[str, int] = {}
    for book_id, key in conn.execute(sa.text("SELECT id, key FROM documents ORDER BY id")).all():
        survivors.setdefault(_book_key(key), book_id)

    for book_id, key in conn.execute(sa.text("SELECT id, key FROM documents ORDER BY id")).all():
        keeper = survivors[_book_key(key)]
        if keeper == book_id:
            continue
        conn.execute(
            sa.text("UPDATE chapters SET document_id = :keeper WHERE document_id = :old"),
            {"keeper": keeper, "old": book_id},
        )
        conn.execute(sa.text("DELETE FROM documents WHERE id = :id"), {"id": book_id})

    for canonical_key, book_id in survivors.items():
        conn.execute(
            sa.text("UPDATE documents SET key = :key WHERE id = :id"),
            {"key": canonical_key, "id": book_id},
        )

    # Имя сайта хранится отдельно и участвует в опознании источника.
    for source_id, site in conn.execute(
        sa.text("SELECT id, site FROM sources WHERE site LIKE 'www.%'")
    ).all():
        conn.execute(
            sa.text("UPDATE sources SET site = :site WHERE id = :id"),
            {"site": site[4:], "id": source_id},
        )


def downgrade() -> None:
    """Пусто, и это не забывчивость.

    Схему миграция не трогает — только приводит данные к канону. Возвращать
    тут нечего: прежние написания адресов не сохранялись, а восстановить из
    `https://51shucheng.net/…`, стояло там `www.` или нет, невозможно.

    Ронять откат ради этого нельзя: цепочку миграций откатывают целиком, и
    отказ здесь заблокировал бы все, что накатаны после. Канонические адреса
    старому коду не мешают — он просто перестанет их канонизировать.
    """

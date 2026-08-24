"""document key

Revision ID: c5a70b41e8d2
Revises: a3f21c7d9e04
Create Date: 2026-08-22 10:12:31.884019

Своего ключа у книги не было: главы группировались перебором соседей с общим
префиксом адреса (RFC §7). Пока книга заводилась одной главой, это стоило
ничего; с обходом цепочкой у книги появились порядок, границы и список — и
выводить её тождество сканированием каждый раз заново стало и дорого, и
ненадёжно.

Ключ — адрес книги на сайте, то есть URL главы без последнего сегмента: то же
самое, что вычислял `book_prefix`, только теперь записанное.

Засыпка идёт по существующим главам, а не по календарю: у каждой книги в базе
есть хотя бы одна глава, а её адрес и есть источник ключа. Книге без глав
(такой быть не должно, но пустая база — не повод падать при накате) достаётся
заглушка по её же id: она уникальна и ни на что не претендует.

Колонка добавляется в три шага, потому что SQLite не умеет ALTER с UNIQUE и
NOT NULL: сначала пустая, потом засыпка, потом пересоздание таблицы с
ограничениями. Порядок обратный сломал бы накат на непустой базе.
"""

from collections.abc import Sequence
from urllib.parse import urlparse

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5a70b41e8d2"
down_revision: str | Sequence[str] | None = "a3f21c7d9e04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _book_prefix(url: str) -> str:
    """Адрес книги: URL главы без последнего сегмента.

    Повторяет `app.services.chapters.book_prefix` намеренно: миграция обязана
    разбираться сама, а не зависеть от кода приложения, который к моменту
    следующего наката может измениться.
    """
    parsed = urlparse(url)
    path = parsed.path.rsplit("/", 1)[0]
    return f"{parsed.scheme}://{parsed.netloc}{path}/"


def _documents_with_nullable_key() -> sa.Table:
    """Таблица `documents` сразу после добавления пустой колонки."""
    return sa.Table(
        "documents",
        sa.MetaData(),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=1024), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("lang", sa.String(length=8), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def _backfill() -> None:
    """Проставить ключ по адресу любой главы книги."""
    bind = op.get_bind()
    documents = bind.execute(sa.text("select id from documents")).scalars().all()

    for document_id in documents:
        url = bind.execute(
            sa.text("select url from chapters where document_id = :id order by id limit 1"),
            {"id": document_id},
        ).scalar()
        key = _book_prefix(url) if url else f"document:{document_id}"
        bind.execute(
            sa.text("update documents set key = :key where id = :id"),
            {"key": key, "id": document_id},
        )


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("documents", sa.Column("key", sa.String(length=1024), nullable=True))
    _backfill()

    with op.batch_alter_table(
        "documents", copy_from=_documents_with_nullable_key(), recreate="always"
    ) as batch_op:
        batch_op.alter_column("key", existing_type=sa.String(length=1024), nullable=False)
        batch_op.create_unique_constraint("uq_documents_key", ["key"])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.drop_constraint("uq_documents_key", type_="unique")
        batch_op.drop_column("key")

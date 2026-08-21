"""english mode and speech

Revision ID: a3f21c7d9e04
Revises: 5b2e9baa556e
Create Date: 2026-08-21 11:20:07.418355

Три изменения одной темы — английский режим и озвучка:

* `chapters.lang` — язык оригинала. Существующим главам проставляется `zh`
  через `server_default`: до этой миграции других и не было;
* `chapters.next_chapter_url` — вход в обход книги по ссылке «следующая глава»;
* `speech_usage` — журнал расходов на синтез речи, отдельно от перевода:
  тариф другой, и складывать их в одну сумму значит получить число, которое
  не соответствует ни одному счёту.

`copy_from` здесь обязателен. SQLite не умеет добавлять CHECK через ALTER,
поэтому batch-режим пересоздаёт таблицу — а пересоздавая её по отражению, он
потерял бы то, чего в отражении не видно. Явное описание прежней таблицы
избавляет от догадок о том, что именно отразилось.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f21c7d9e04"
down_revision: str | Sequence[str] | None = "5b2e9baa556e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _chapters_before() -> sa.Table:
    """Таблица `chapters` в том виде, в каком она пришла из 774cd4aef341."""
    return sa.Table(
        "chapters",
        sa.MetaData(),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("url", sa.String(length=1024), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tokens_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_kind", sa.String(length=32), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("chars_sent", sa.Integer(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=True),
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
        sa.CheckConstraint(
            "status in ('fetching','segmented','translating','ready','failed')",
            name="ck_chapters_status",
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
        sa.Index("ix_chapters_document_idx", "document_id", "idx"),
    )


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table(
        "chapters", copy_from=_chapters_before(), recreate="always"
    ) as batch_op:
        batch_op.add_column(
            sa.Column("lang", sa.String(length=8), nullable=False, server_default="zh")
        )
        batch_op.add_column(sa.Column("next_chapter_url", sa.String(length=1024), nullable=True))
        batch_op.create_check_constraint("ck_chapters_lang", "lang in ('zh','en')")

    op.create_table(
        "speech_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("voice", sa.String(length=32), nullable=False),
        sa.Column("chars_sent", sa.Integer(), nullable=False),
        sa.Column("chapter_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("speech_usage", schema=None) as batch_op:
        batch_op.create_index("ix_speech_usage_created", ["created_at"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("speech_usage", schema=None) as batch_op:
        batch_op.drop_index("ix_speech_usage_created")

    op.drop_table("speech_usage")

    with op.batch_alter_table("chapters", schema=None) as batch_op:
        batch_op.drop_constraint("ck_chapters_lang", type_="check")
        batch_op.drop_column("next_chapter_url")
        batch_op.drop_column("lang")

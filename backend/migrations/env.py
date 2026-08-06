"""Окружение Alembic.

URL берётся из настроек приложения, а не из alembic.ini: путь к базе зависит
от окружения (локально — backend/data, на ВМ — /opt/chinese-reader/data),
и держать его в двух местах значит рано или поздно разъехаться.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import settings
from app.db import models  # noqa: F401  — регистрирует модели в metadata
from app.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{settings.db_path}"


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite не умеет ALTER для большинства случаев: batch-режим
            # пересоздаёт таблицу. Без него любая правка колонки развалится.
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

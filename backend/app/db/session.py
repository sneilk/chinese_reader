"""Подключение к SQLite.

Один пользователь, один писатель — конкуренции за запись нет, поэтому SQLite
достаточно (RFC §10). WAL и `foreign_keys` включаются явно: без первого
чтение блокируется записью, без второго SQLite молча игнорирует внешние ключи.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings


def _make_engine() -> Engine:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{settings.db_path}", future=True)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record) -> None:
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.close()


def get_session() -> Iterator[Session]:
    """Зависимость FastAPI."""
    with SessionLocal() as session:
        yield session

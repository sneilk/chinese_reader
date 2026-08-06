"""Базовый класс моделей.

Отдельным модулем, чтобы Alembic мог импортировать метаданные, не втягивая
приложение целиком.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass

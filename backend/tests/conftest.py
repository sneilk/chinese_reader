"""Общие настройки тестов.

Главное здесь — не дать тестам писать в боевой `data/`. Ручка сохранения
слова подкладывает его в `userdict.txt` (T2.7), и без этой защиты каждый
прогон дописывал бы тестовые слова в рабочий словарь разработчика: замечено
по строке `窗户 10000`, приехавшей в него из тестов.
"""

import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Каждому тесту — свой каталог данных."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    return data

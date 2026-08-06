"""Настройки приложения.

Всё, что зависит от окружения, живёт здесь и только здесь. На сервере значения
приходят из /etc/chinese-reader/env через systemd EnvironmentFile.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Данные: SQLite, профиль браузера, словарные дампы. Вне git.
    data_dir: Path = Path("data")

    # Переводчик
    yc_translate_api_key: str = ""
    yc_folder_id: str = ""
    # Ограничение API: суммарно 10 000 символов на запрос
    translate_batch_chars: int = 9000
    translate_timeout_seconds: float = 30.0
    # Ретраи только на перегрузку и таймауты той стороны; паузы 5 и 20 секунд.
    translate_retries: int = 2
    # Мягкие потолки расходов, см. RFC §6
    translate_max_chars_per_chapter: int = 30_000
    translate_max_chars_per_month: int = 3_000_000

    # Загрузчик. Headless не работает: Cloudflare отдаёт 403 даже с живой
    # cf_clearance, проверено в T0.3. Менять только вместе с проверкой.
    browser_headless: bool = False
    browser_display: str = ":99"
    browser_nav_timeout_ms: int = 45_000
    browser_retries: int = 2
    fetch_delay_seconds: float = 2.0
    max_pages_per_chapter: int = 20

    @property
    def db_path(self) -> Path:
        return self.data_dir / "chinese_reader.db"

    @property
    def browser_profile_dir(self) -> Path:
        return self.data_dir / "browser-profile"

    @property
    def userdict_path(self) -> Path:
        """Словарь приложения в формате jieba. Собирается из dict_entries."""
        return self.data_dir / "userdict.txt"


settings = Settings()

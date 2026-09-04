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

    # Озвучка русского перевода (Yandex SpeechKit). Ключ отдельный: роли
    # `ai.translate.user` для синтеза не хватает, нужна `ai.speechkit-tts.user`.
    # Пустой означает «взять ключ переводчика» — это рабочий случай, когда у
    # одного сервисного аккаунта выданы обе роли.
    yc_speech_api_key: str = ""
    speech_voice: str = "alena"
    # Интонация: у alena это neutral и good. Пустая — голос как есть.
    speech_emotion: str = ""
    speech_speed: float = 1.0
    # mp3, а не oggopus: opus не играет в Safari на iPhone, а телефон здесь
    # основной сценарий чтения, а не исключение из него.
    speech_format: str = "mp3"
    speech_timeout_seconds: float = 30.0
    # Ограничение API — 5000 символов на запрос; берём с запасом.
    speech_max_chars_per_request: int = 4000
    speech_max_chars_per_month: int = 500_000
    # Потолок кэша mp3. Кэш вечный по смыслу — текст перевода не меняется, —
    # но диск конечен: книга в полтысячи глав озвучивается в гигабайты, а
    # рядом на том же диске лежат база и семь её копий.
    speech_cache_max_bytes: int = 2 * 1024**3

    # Загрузчик. Headless не работает: Cloudflare отдаёт 403 даже с живой
    # cf_clearance, проверено в T0.3. Менять только вместе с проверкой.
    browser_headless: bool = False
    browser_display: str = ":99"
    browser_nav_timeout_ms: int = 45_000
    browser_retries: int = 2
    fetch_delay_seconds: float = 2.0
    max_pages_per_chapter: int = 20
    # Потолок на обход книги по ссылке «следующая глава» за один запрос.
    # Пятьдесят — это примерно вечер чтения вперёд: столько имеет смысл просить
    # с экрана главы, не уходя в выгрузку книги целиком.
    max_chapters_per_run: int = 50
    # Потолок на выгрузку книги целиком. Он другой по смыслу: не «сколько
    # попросить», а «где остановиться, если ссылки вперёд не кончаются».
    # Книга-образец — 550 глав, кольцо ссылок ловится отдельно, а этот предел
    # существует на случай, когда сайт отдаёт цепочку без конца.
    max_chapters_per_book: int = 2000

    # Сколько держать открытым видимое окно браузера, в котором проверку сайта
    # проходят руками. Минуты — столько занимает капча, если её вообще дали.
    browser_check_timeout_seconds: float = 180.0

    # Логи. Уровень живёт в настройке, а не в коде: поднять DEBUG на боевой
    # машине должно стоить перезапуск юнита, а не выкладку.
    log_level: str = "INFO"
    log_requests: bool = True

    # Точный путь к тексту главы на novelarrow. Пустой означает «искать
    # эвристикой» (adapters/dom.py). Живёт в конфиге, а не в коде, потому что
    # классы Next.js меняются вместе со сборкой фронта сайта, и подстроиться
    # под них должно быть правкой строки в /etc/chinese-reader/env.
    novelarrow_content_xpath: str = ""

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

    @property
    def tts_cache_dir(self) -> Path:
        """Синтезированные mp3. Кэш вечный: текст перевода не меняется."""
        return self.data_dir / "tts"

    @property
    def browser_check_screenshot(self) -> Path:
        """Снимок экрана последней ручной проверки сайта.

        Файл один и перезаписывается: интересен всегда последний, а история
        снимков чужих страниц никому не нужна и занимает место.
        """
        return self.data_dir / "browser-check.png"

    @property
    def speech_api_key(self) -> str:
        return self.yc_speech_api_key or self.yc_translate_api_key


settings = Settings()

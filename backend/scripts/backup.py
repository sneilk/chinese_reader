"""Снять резервную копию: PYTHONPATH=. python scripts/backup.py [--keep N]

Копируется база и профиль браузера — то, что нельзя восстановить заново.
Словарные дампы и виртуальное окружение в копию не идут: они качаются и
ставятся скриптами, и место в хранилище им ни к чему.

**Базу нельзя копировать через `cp`.** В режиме WAL часть свежих страниц
лежит не в файле базы, а в журнале, и обычная копия окажется либо битой, либо
отставшей — молча, без единой ошибки. Штатный способ — `.backup` (в Python
это `Connection.backup`): он снимает согласованный снимок, не мешая работе
сервиса и не требуя его остановки.

Профиль браузера — каталог, и в нём сотни мегабайт кэша, который
восстанавливать бессмысленно: он наберётся сам. В архив идут куки и настройки,
ради которых профиль вообще хранится (T0.3: `cf_clearance` переживает
перезагрузку именно благодаря ему).

Что этот скрипт **не делает**: не отправляет копию в Object Storage. Выгрузка
через `rclone` и ночной таймер — часть развёртывания (T2.9), их место в
systemd-юните рядом с сервисом.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.services.speech import prune_cache

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("backup")

# Кэш браузера в копию не идёт: он наберётся сам, а весит больше всего
# остального вместе взятого.
PROFILE_SKIP = {
    "Cache",
    "Code Cache",
    "GPUCache",
    "ShaderCache",
    "GrShaderCache",
    "DawnCache",
    "component_crx_cache",
    "Crashpad",
}


def backup_database(db: Path, dest: Path) -> Path:
    """Снять согласованную копию базы, не останавливая сервис."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return dest


def verify(db: Path) -> bool:
    """Проверить копию до того, как на неё понадеются.

    Копия, которую никто не открывал, — это надежда, а не резерв.
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        result = con.execute("PRAGMA integrity_check").fetchone()
        return bool(result) and result[0] == "ok"
    except sqlite3.DatabaseError as e:
        log.error("копия не открывается: %s", e)
        return False
    finally:
        con.close()


def archive_profile(profile: Path, dest: Path) -> Path | None:
    """Упаковать профиль браузера без кэша."""
    if not profile.exists():
        log.info("профиля браузера нет, пропускаю: %s", profile)
        return None

    def keep(item: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = set(Path(item.name).parts)
        return None if parts & PROFILE_SKIP else item

    with tarfile.open(dest, "w:gz") as tar:
        tar.add(profile, arcname=profile.name, filter=keep)
    return dest


def rotate(directory: Path, prefix: str, keep: int) -> list[Path]:
    """Оставить `keep` последних копий. Возвращает удалённые."""
    copies = sorted(directory.glob(f"{prefix}-*"), key=lambda p: p.name)
    extra = copies[:-keep] if keep > 0 else copies
    for path in extra:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        log.info("удалена старая копия: %s", path.name)
    return extra


def run(dest_dir: Path, keep: int, stamp: str | None = None) -> Path:
    stamp = stamp or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = dest_dir / f"backup-{stamp}"
    target.mkdir(parents=True, exist_ok=True)

    db_copy = backup_database(settings.db_path, target / "chinese_reader.db")
    if not verify(db_copy):
        raise SystemExit(f"копия базы не прошла проверку целостности: {db_copy}")
    log.info("база скопирована и проверена: %s", db_copy)

    profile = archive_profile(settings.browser_profile_dir, target / "browser-profile.tar.gz")
    if profile is not None:
        log.info("профиль браузера упакован: %s", profile)

    rotate(dest_dir, "backup", keep)

    # Кэш озвучки в копию не идёт — он восстановим синтезом, — но растёт на том
    # же диске, что и копии. Ночь единственное время, когда обходить тысячи
    # файлов не жалко, поэтому уборка живёт здесь, а не в самом сервисе.
    removed, freed = prune_cache()
    if removed:
        log.info("кэш озвучки ужат: %s файлов, %s МБ", removed, freed // 1024 // 1024)

    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Резервная копия базы и профиля браузера")
    parser.add_argument("--dest", type=Path, default=settings.data_dir / "backups")
    parser.add_argument("--keep", type=int, default=7, help="сколько копий держать")
    args = parser.parse_args()

    target = run(args.dest, args.keep)
    print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())

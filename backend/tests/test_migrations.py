"""Миграции должны накатываться и откатываться на чистой базе.

Alembic запускается подпроцессом с подменённым DATA_DIR: настройки читаются
при импорте, и переопределять их в том же процессе значит бороться с
кэшированием ради ничего.
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ALEMBIC = BACKEND / ".venv" / "bin" / "alembic"


def _alembic(args: list[str], data_dir: Path) -> subprocess.CompletedProcess:
    exe = [str(ALEMBIC)] if ALEMBIC.exists() else [sys.executable, "-m", "alembic"]
    return subprocess.run(
        exe + args,
        cwd=BACKEND,
        env={"PATH": "/usr/bin:/bin", "DATA_DIR": str(data_dir), "HOME": str(data_dir)},
        capture_output=True,
        text=True,
        check=False,
    )


def _tables(db: Path) -> set[str]:
    if not db.exists():
        return set()
    con = sqlite3.connect(db)
    try:
        return {
            r[0]
            for r in con.execute(
                "select name from sqlite_master where type='table' and name not like 'alembic%'"
            )
        }
    finally:
        con.close()


def test_upgrade_then_downgrade(tmp_path):
    db = tmp_path / "chinese_reader.db"

    up = _alembic(["upgrade", "head"], tmp_path)
    assert up.returncode == 0, up.stderr
    assert _tables(db) == {
        "sources",
        "documents",
        "chapters",
        "sentences",
        "dict_entries",
        "translation_usage",
        "user_words",
        "contexts",
        "word_occurrences",
    }

    down = _alembic(["downgrade", "base"], tmp_path)
    assert down.returncode == 0, down.stderr
    assert _tables(db) == set()

    again = _alembic(["upgrade", "head"], tmp_path)
    assert again.returncode == 0, again.stderr
    assert "chapters" in _tables(db)

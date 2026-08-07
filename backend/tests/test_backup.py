"""Резервная копия базы и профиля.

Проверяется то, ради чего копию вообще снимают: она открывается, содержит
данные и не битая. Отдельно — что копия снимается с базы **в режиме WAL и под
записью**: обычное `cp` в этом месте даёт либо битый файл, либо отставший,
и узнаётся это только при восстановлении.

Ночной таймер, выгрузка через rclone и восстановление на пустой ВМ здесь не
проверяются — это часть развёртывания, и без самой машины их не прогнать.
"""

import sqlite3

import pytest

from app.config import settings
from scripts.backup import archive_profile, backup_database, rotate, run, verify


@pytest.fixture
def live_db(tmp_path):
    """База в WAL с незакоммиченным хвостом в журнале — как на работающем сервисе."""
    path = tmp_path / "live.db"
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("create table chapters (id integer primary key, title text)")
    con.executemany("insert into chapters (title) values (?)", [(f"глава {i}",) for i in range(50)])
    con.commit()
    # Соединение остаётся открытым: часть страниц живёт в -wal, а не в файле.
    yield path
    con.close()


def test_backup_is_readable_and_complete(live_db, tmp_path):
    copy = backup_database(live_db, tmp_path / "copy.db")

    assert verify(copy)
    con = sqlite3.connect(copy)
    try:
        assert con.execute("select count(*) from chapters").fetchone()[0] == 50
    finally:
        con.close()


def test_backup_catches_rows_still_in_wal(live_db, tmp_path):
    """Свежие строки лежат в журнале — простая копия файла их бы не увидела."""
    con = sqlite3.connect(live_db)
    con.execute("insert into chapters (title) values ('свежая глава')")
    con.commit()

    copy = backup_database(live_db, tmp_path / "copy.db")
    con.close()

    check = sqlite3.connect(copy)
    try:
        titles = {r[0] for r in check.execute("select title from chapters")}
    finally:
        check.close()
    assert "свежая глава" in titles


def test_verify_rejects_broken_copy(tmp_path):
    """Копия, которую никто не открывал, — надежда, а не резерв."""
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"not a database at all")
    assert not verify(broken)


def test_profile_archive_skips_cache(tmp_path):
    import tarfile

    profile = tmp_path / "browser-profile"
    (profile / "Default").mkdir(parents=True)
    (profile / "Default" / "Cookies").write_text("куки", encoding="utf-8")
    (profile / "Cache").mkdir()
    (profile / "Cache" / "big-file").write_bytes(b"0" * 1024)

    archive = archive_profile(profile, tmp_path / "profile.tar.gz")

    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert any(n.endswith("Cookies") for n in names), "куки — то, ради чего профиль хранят"
    assert not any("Cache" in n for n in names), "кэш наберётся сам"


def test_missing_profile_is_not_an_error(tmp_path):
    assert archive_profile(tmp_path / "нет-такого", tmp_path / "out.tar.gz") is None


def test_rotation_keeps_last(tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir()
    for stamp in ["20260801-000000", "20260802-000000", "20260803-000000"]:
        (backups / f"backup-{stamp}").mkdir()

    removed = rotate(backups, "backup", keep=2)

    assert [p.name for p in removed] == ["backup-20260801-000000"]
    assert sorted(p.name for p in backups.iterdir()) == [
        "backup-20260802-000000",
        "backup-20260803-000000",
    ]


def test_run_makes_usable_copy(tmp_path):
    # Каталог данных подменён общей фикстурой из conftest: тесты не должны
    # писать в боевой data/.
    con = sqlite3.connect(settings.db_path)
    con.execute("create table words (id integer primary key, headword text)")
    con.execute("insert into words (headword) values ('学习')")
    con.commit()
    con.close()

    target = run(tmp_path / "backups", keep=3, stamp="20260807-120000")

    copy = target / "chinese_reader.db"
    assert copy.exists() and verify(copy)
    check = sqlite3.connect(copy)
    try:
        assert check.execute("select headword from words").fetchone()[0] == "学习"
    finally:
        check.close()

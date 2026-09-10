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
        "speech_usage",
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


# --- канонизация адресов на живых данных ---
#
# Миграция не меняет схему, а правит данные, и проверять её надо на данных.
# Главное здесь — что выживает: за перевод заплачено, и выбрасывать надо ту
# главу, за которую не платили.


def _seed_duplicates(db: Path) -> None:
    """Три написания одной главы, книга в двух написаниях и сохранённое слово."""
    con = sqlite3.connect(db)
    con.executescript(
        """
        INSERT INTO sources (id, kind, site, lang) VALUES (1, 'web', 'www.51shucheng.net', 'zh');
        INSERT INTO documents (id, source_id, key, lang)
             VALUES (1, 1, 'https://www.51shucheng.net/renwen/kniga/', 'zh'),
                    (2, 1, 'https://51shucheng.net/renwen/kniga/', 'zh');

        -- Одна и та же глава тремя способами. Богатая — вторая: у неё перевод.
        INSERT INTO chapters
               (id, document_id, url, lang, status, content,
                next_chapter_url, chars_sent)
        VALUES (1, 1, 'https://www.51shucheng.net/renwen/kniga/1.html',
                'zh', 'segmented', 'текст', NULL, 0),
               (2, 2, 'https://51shucheng.net/renwen/kniga/1.html',
                'zh', 'ready', 'текст',
                'https://www.51shucheng.net/renwen/kniga/2.html', 120),
               (3, 1, 'HTTPS://51shucheng.NET/renwen/kniga/1.html#top',
                'zh', 'fetching', NULL, NULL, 0);

        INSERT INTO sentences (id, chapter_id, idx, start_offset, end_offset, translation)
             VALUES (1, 2, 0, 0, 5, 'перевод'),
                    (2, 1, 0, 0, 5, NULL);

        INSERT INTO user_words (id, lang, headword, status) VALUES (1, 'zh', '窗户', 'new');
        INSERT INTO contexts
               (id, user_word_id, chapter_id, sentence_id,
                sentence, offset_start, offset_end)
        VALUES (1, 1, 1, 2, 'предложение с 窗户', 0, 2);
        """
    )
    con.commit()
    con.close()


def test_canonical_urls_keeps_the_richest_duplicate(tmp_path):
    assert _alembic(["upgrade", "c5a70b41e8d2"], tmp_path).returncode == 0
    db = tmp_path / "chinese_reader.db"
    _seed_duplicates(db)

    done = _alembic(["upgrade", "head"], tmp_path)
    assert done.returncode == 0, done.stderr

    con = sqlite3.connect(db)
    chapters = con.execute("SELECT id, url, document_id FROM chapters").fetchall()
    assert len(chapters) == 1, "три написания одной главы должны стать одной записью"
    survivor_id, url, document_id = chapters[0]

    # Выжила та, за которую заплачено переводом.
    assert survivor_id == 2
    assert url == "https://51shucheng.net/renwen/kniga/1.html"
    assert con.execute(
        "SELECT translation FROM sentences WHERE chapter_id = ?", (survivor_id,)
    ).fetchone()[0] == "перевод"

    # Ссылка вперёд — тот же ключ, иначе соседку не найти.
    assert con.execute(
        "SELECT next_chapter_url FROM chapters WHERE id = ?", (survivor_id,)
    ).fetchone()[0] == "https://51shucheng.net/renwen/kniga/2.html"

    # Книга одна, и выжившая глава лежит в ней.
    books = con.execute("SELECT id, key FROM documents").fetchall()
    assert len(books) == 1
    assert books[0][1] == "https://51shucheng.net/renwen/kniga/"
    assert document_id == books[0][0]

    assert con.execute("SELECT site FROM sources").fetchone()[0] == "51shucheng.net"
    con.close()


def test_canonical_urls_does_not_touch_the_dictionary(tmp_path):
    """Слово переживает удаление главы — ровно затем контекст и хранит копию."""
    assert _alembic(["upgrade", "c5a70b41e8d2"], tmp_path).returncode == 0
    db = tmp_path / "chinese_reader.db"
    _seed_duplicates(db)
    assert _alembic(["upgrade", "head"], tmp_path).returncode == 0

    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM user_words").fetchone()[0] == 1
    sentence, chapter_id = con.execute(
        "SELECT sentence, chapter_id FROM contexts WHERE id = 1"
    ).fetchone()

    assert sentence == 'предложение с 窗户', "текст контекста обязан уцелеть"
    assert chapter_id is None, "ссылка на удалённую главу обнуляется, а не висит"
    con.close()

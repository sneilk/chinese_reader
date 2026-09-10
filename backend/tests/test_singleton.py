"""Место единственного процесса.

Инвариант «один процесс» нигде не был записан, а держится на нём и профиль
браузера, и реестр обходов, и единственный писатель в SQLite. Ломается он
молча, поэтому проверяется здесь.
"""

import multiprocessing

from app.singleton import claim


def test_first_process_takes_the_place(tmp_path):
    assert claim(tmp_path) is True


def test_the_same_process_may_ask_twice(tmp_path):
    """Перезапуск lifespan в тестах не должен выглядеть как второй сервис."""
    assert claim(tmp_path) is True
    assert claim(tmp_path) is True


def _second(data_dir, out):
    out.put(claim(data_dir))


def test_second_process_is_refused(tmp_path):
    """Тот самый случай: --workers 2 в юните."""
    assert claim(tmp_path) is True

    ctx = multiprocessing.get_context("spawn")
    out = ctx.Queue()
    other = ctx.Process(target=_second, args=(tmp_path, out))
    other.start()
    other.join(timeout=30)

    assert out.get(timeout=5) is False, "второй процесс обязан узнать, что место занято"


def test_lock_survives_a_missing_directory(tmp_path):
    """Каталог данных может ещё не существовать — это первый запуск."""
    assert claim(tmp_path / "data") is True

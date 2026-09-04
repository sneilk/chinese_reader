"""Учёт идущих выгрузок книги.

Логика здесь на десяток строк, но два её правила видны только отсюда: через
API их не поймать, потому что в тестах фоновая задача успевает закончиться
раньше, чем приходит ответ.

**Второй обход той же книги не запускается.** Он не ускорил бы ничего —
загрузчик ходит на сайт по одному запросу за раз, — зато пошёл бы по той же
цепочке и удвоил счётчик, то есть испортил единственную картинку, по которой
читатель судит о часовой работе.

**Закончившийся обход не мешает следующему.** Иначе «выгрузить книгу» стало бы
одноразовым действием: дочитав до конца загруженного, читатель нажал бы кнопку
и не получил ничего.
"""

import pytest

from app.domain import ErrorKind
from app.services import walks


@pytest.fixture(autouse=True)
def clean():
    walks.reset()
    yield
    walks.reset()


def test_nothing_is_known_about_a_book_never_walked():
    assert walks.current(7) is None


def test_start_marks_the_walk_running():
    walk = walks.start(7, limit=2000)

    assert walk is not None
    assert walk.running is True
    assert walk.loaded == 0
    assert walk.limit == 2000
    assert walks.current(7) is walk


def test_second_start_while_running_is_refused():
    walks.start(7, limit=2000)

    assert walks.start(7, limit=2000) is None


def test_the_running_walk_survives_a_refused_start():
    """Отказ во втором запуске не должен обнулить счётчик первого."""
    walks.start(7, limit=2000)
    walks.note_loaded(7)

    walks.start(7, limit=2000)

    assert walks.current(7).loaded == 1


def test_another_book_walks_in_parallel():
    """Занят обход книги, а не сервис: две книги — два разных счётчика."""
    walks.start(7, limit=100)

    assert walks.start(8, limit=100) is not None
    assert walks.current(7) is not walks.current(8)


def test_progress_counts_loaded_chapters():
    walks.start(7, limit=2000)

    for _ in range(3):
        walks.note_loaded(7)

    assert walks.current(7).loaded == 3


def test_progress_of_an_unknown_book_is_ignored():
    """Удалённая книга не должна воскресать от опоздавшей главы."""
    walks.note_loaded(99)

    assert walks.current(99) is None


def test_finish_closes_the_walk():
    walks.start(7, limit=2000)

    walks.finish(7)

    walk = walks.current(7)
    assert walk.running is False
    assert walk.stopped_by is None, "конец книги — не отказ"
    assert walk.finished_at is not None


def test_finish_remembers_what_stopped_it():
    walks.start(7, limit=2000)

    walks.finish(7, ErrorKind.CHALLENGE)

    assert walks.current(7).stopped_by == ErrorKind.CHALLENGE


def test_a_finished_walk_can_be_started_again():
    """Иначе «выгрузить книгу» стало бы одноразовым действием."""
    walks.start(7, limit=2000)
    walks.note_loaded(7)
    walks.finish(7)

    again = walks.start(7, limit=2000)

    assert again is not None
    assert again.loaded == 0, "новый обход считает своё, а не продолжает прошлый"


def test_forgetting_a_book_forgets_its_walk():
    walks.start(7, limit=2000)

    walks.forget(7)

    assert walks.current(7) is None


# --- остановка ---


def test_a_running_walk_is_not_asked_to_stop_by_itself():
    walks.start(7, limit=2000)

    assert walks.should_stop(7) is False


def test_asking_to_stop_is_seen_by_the_walk():
    walks.start(7, limit=2000)

    walks.request_stop(7)

    assert walks.should_stop(7) is True
    assert walks.current(7).cancelled is True


def test_stopping_does_not_close_the_walk_by_itself():
    """Закрывает его сам обход, дописав текущую главу, — иначе он врал бы о себе."""
    walks.start(7, limit=2000)

    walks.request_stop(7)

    assert walks.current(7).running is True


def test_a_deleted_book_stops_its_walk():
    """Книги нет — идти некуда, даже если обход об этом ещё не знает."""
    walks.start(7, limit=2000)
    walks.forget(7)

    assert walks.should_stop(7) is True


def test_shutdown_stops_everything():
    """В том числе обход, записи о котором здесь нет: «ещё N глав» с экрана главы."""
    walks.start(7, limit=2000)
    walks.start(8, limit=2000)

    asked = walks.stop_all()

    assert asked == 2
    assert walks.shutting_down() is True
    assert walks.should_stop(7) is True
    assert walks.should_stop(99) is True


def test_shutdown_does_not_count_the_finished():
    walks.start(7, limit=2000)
    walks.finish(7)

    assert walks.stop_all() == 0


def test_nothing_is_shutting_down_by_default():
    assert walks.shutting_down() is False

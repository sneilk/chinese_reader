"""Заведение главы: идемпотентность, группировка в книгу и догадка о языке.

Через ручки это проверяется по касательной, а решается здесь и молча. Три
вещи, каждая из которых ломается незаметно.

**Идемпотентность по URL** — то, на чём держится «за одной главой ходим один
раз» (концепция §1.3). Сломайся она, и повторное открытие главы шло бы на сайт
заново, а на 51shucheng это лишний шанс получить проверку Cloudflare.

**Группировка в книгу по её ключу** — адресу на сайте, то есть URL главы без
последнего сегмента. Разъедься это, и обход книги завёл бы по книге на главу,
а оглавление рассыпалось бы на двадцать списков по одной строке.

**Язык при заведении — догадка по адаптеру, а не факт.** Факт появляется после
разбора страницы (`pipeline.apply_language`); здесь важно лишь то, что догадка
разумна и что у generic-адреса она не мешает.
"""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models import Chapter, Document, Source
from app.domain import ChapterStatus, Language
from app.services.chapters import book_prefix, get_or_create_chapter, guess_language

SHUCHENG = "https://www.51shucheng.net/renwen/kniga/12345.html"
SHUCHENG_SIBLING = "https://www.51shucheng.net/renwen/kniga/12346.html"
SHUCHENG_OTHER_BOOK = "https://www.51shucheng.net/renwen/drugaya/1.html"
NOVELARROW = "https://novelarrow.com/novel/the-long-cartography/chapter-12"
NOVELARROW_SIBLING = "https://novelarrow.com/novel/the-long-cartography/chapter-13"


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'chapters.db'}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, class_=Session, expire_on_commit=False)() as s:
        yield s


def count(session, model) -> int:
    return len(session.scalars(select(model)).all())


# --- адрес книги ---


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # `www.` уходит: адрес книги считается от канонического, иначе одна
        # книга раздваивалась бы от написания ссылки.
        (SHUCHENG, "https://51shucheng.net/renwen/kniga/"),
        (NOVELARROW, "https://novelarrow.com/novel/the-long-cartography/"),
        ("https://example.com/one.html", "https://example.com/"),
        ("https://example.com/a/b/c/d", "https://example.com/a/b/c/"),
    ],
)
def test_book_prefix(url, expected):
    assert book_prefix(url) == expected


def test_book_prefix_keeps_the_host():
    """Одинаковые пути на разных сайтах — разные книги, а не одна."""
    assert book_prefix("https://a.com/x/1") != book_prefix("https://b.com/x/1")


# --- догадка о языке ---


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (SHUCHENG, Language.ZH),
        (NOVELARROW, Language.EN),
        # У generic языка нет — берётся китайский, с которого всё начиналось.
        # Догадка всё равно будет уточнена после разбора страницы.
        ("https://example.com/story/1", Language.ZH),
    ],
)
def test_guess_language(url, expected):
    assert guess_language(url) is expected


# --- заведение ---


def test_creates_chapter_with_guessed_language(session):
    chapter, created = get_or_create_chapter(session, NOVELARROW)

    assert created is True
    assert chapter.id is not None
    assert chapter.lang == Language.EN
    assert chapter.status == ChapterStatus.FETCHING
    assert chapter.url == NOVELARROW


def test_repeat_returns_the_same_chapter(session):
    """На этом держится «за одной главой ходим один раз»."""
    first, created_first = get_or_create_chapter(session, SHUCHENG)
    second, created_second = get_or_create_chapter(session, SHUCHENG)

    assert created_first is True
    assert created_second is False
    assert second.id == first.id
    assert count(session, Chapter) == 1


def test_repeat_does_not_reset_progress(session):
    """Иначе повторный запрос стирал бы уже загруженную главу."""
    chapter, _ = get_or_create_chapter(session, SHUCHENG)
    chapter.status = ChapterStatus.READY
    chapter.content = "текст главы"
    session.commit()

    again, created = get_or_create_chapter(session, SHUCHENG)

    assert created is False
    assert again.status == ChapterStatus.READY
    assert again.content == "текст главы"


# --- книга и источник ---


def test_sibling_chapters_share_one_document(session):
    get_or_create_chapter(session, SHUCHENG)
    get_or_create_chapter(session, SHUCHENG_SIBLING)

    assert count(session, Document) == 1
    assert count(session, Chapter) == 2


def test_different_books_get_different_documents(session):
    get_or_create_chapter(session, SHUCHENG)
    get_or_create_chapter(session, SHUCHENG_OTHER_BOOK)

    assert count(session, Document) == 2


def test_one_source_per_site(session):
    """Книг у сайта много, а сайт один: источник переиспользуется."""
    get_or_create_chapter(session, SHUCHENG)
    get_or_create_chapter(session, SHUCHENG_OTHER_BOOK)

    assert count(session, Source) == 1
    assert session.scalars(select(Source)).one().site == "51shucheng.net"


def test_different_sites_get_different_sources(session):
    get_or_create_chapter(session, SHUCHENG)
    get_or_create_chapter(session, NOVELARROW)

    assert count(session, Source) == 2
    assert count(session, Document) == 2


def test_document_and_source_inherit_the_guess(session):
    get_or_create_chapter(session, NOVELARROW)

    assert session.scalars(select(Document)).one().lang == Language.EN
    assert session.scalars(select(Source)).one().lang == Language.EN


def test_book_is_recognised_in_any_order(session):
    """Ключ выводится из адреса, поэтому неважно, какую главу открыли первой."""
    get_or_create_chapter(session, NOVELARROW_SIBLING)
    get_or_create_chapter(session, NOVELARROW)

    assert count(session, Document) == 1


def test_book_key_is_its_address(session):
    get_or_create_chapter(session, NOVELARROW)

    assert session.scalars(select(Document)).one().key == book_prefix(NOVELARROW)


def test_source_kind_is_web(session):
    get_or_create_chapter(session, SHUCHENG)
    assert session.scalars(select(Source)).one().kind == "web"


# --- место главы в книге ---
#
# `idx` — не номер главы на сайте: вывести его неоткуда, слаг у Next.js его не
# содержит. Это позиция в известной нам цепочке, и нужна она ровно за тем, чтобы
# показать главы книги в том порядке, в каком их читают.


def test_first_chapter_of_a_book_starts_the_count(session):
    chapter, _ = get_or_create_chapter(session, NOVELARROW)
    assert chapter.idx == 0


def test_next_chapter_stands_behind_the_one_pointing_at_it(session):
    """Так его расставляет и обход цепочкой, и кнопка «загрузить следующую»."""
    first, _ = get_or_create_chapter(session, NOVELARROW)
    first.next_chapter_url = NOVELARROW_SIBLING
    session.commit()

    second, _ = get_or_create_chapter(session, NOVELARROW_SIBLING)

    assert (first.idx, second.idx) == (0, 1)


def test_chain_keeps_counting(session):
    third_url = f"{NOVELARROW.rsplit('-', 1)[0]}-14"
    first, _ = get_or_create_chapter(session, NOVELARROW)
    first.next_chapter_url = NOVELARROW_SIBLING
    session.commit()
    second, _ = get_or_create_chapter(session, NOVELARROW_SIBLING)
    second.next_chapter_url = third_url
    session.commit()

    third, _ = get_or_create_chapter(session, third_url)

    assert [c.idx for c in (first, second, third)] == [0, 1, 2]


def test_chapter_dropped_into_the_middle_has_no_position(session):
    """Врать про её положение хуже, чем не знать его."""
    get_or_create_chapter(session, NOVELARROW)

    stray, _ = get_or_create_chapter(session, f"{NOVELARROW.rsplit('-', 1)[0]}-99")

    assert stray.idx is None


def test_position_is_not_inherited_from_an_unplaced_predecessor(session):
    """Считать от неизвестного значит выдать догадку за цепочку."""
    get_or_create_chapter(session, NOVELARROW)
    stray_url = f"{NOVELARROW.rsplit('-', 1)[0]}-99"
    stray, _ = get_or_create_chapter(session, stray_url)
    stray.next_chapter_url = NOVELARROW_SIBLING
    session.commit()

    following, _ = get_or_create_chapter(session, NOVELARROW_SIBLING)

    assert stray.idx is None
    assert following.idx is None


def test_predecessor_from_another_book_does_not_count(session):
    """Ссылка через границу книги — это не её цепочка."""
    alien, _ = get_or_create_chapter(session, SHUCHENG)
    alien.next_chapter_url = NOVELARROW
    session.commit()

    chapter, _ = get_or_create_chapter(session, NOVELARROW)

    assert chapter.idx == 0, "первая глава своей книги, а не вторая чужой"


# --- тождество главы ---
#
# `chapters.url` — это ключ идемпотентности: по нему повторный POST не идёт на
# сайт, а обход не заводит дубль. Пока сравнивались сырые строки, одна и та же
# страница заводилась дважды от любой мелочи в написании ссылки.


@pytest.mark.parametrize(
    "variant",
    [
        "https://www.51shucheng.net/renwen/kniga/12345.html",
        "https://51shucheng.net/renwen/kniga/12345.html",
        "HTTPS://51shucheng.NET/renwen/kniga/12345.html",
        "https://51shucheng.net:443/renwen/kniga/12345.html",
        "https://51shucheng.net/renwen/kniga/12345.html#top",
        "  https://51shucheng.net/renwen/kniga/12345.html  ",
    ],
)
def test_one_chapter_however_the_link_is_written(session, variant):
    first, created = get_or_create_chapter(session, SHUCHENG)
    again, created_again = get_or_create_chapter(session, variant)

    assert created is True
    assert created_again is False, f"{variant} завёл вторую главу"
    assert again.id == first.id
    assert count(session, Chapter) == 1


def test_query_is_dropped_where_the_site_says_it_means_nothing(session):
    """`?restore=1` novelarrow приписывает сам — это состояние интерфейса."""
    first, _ = get_or_create_chapter(session, NOVELARROW)
    again, created = get_or_create_chapter(session, f"{NOVELARROW}?restore=1")

    assert created is False
    assert again.id == first.id


def test_query_is_kept_for_an_unknown_site(session):
    """Сайт незнаком, и в запросе может лежать номер страницы.

    Выбросив его, мы склеили бы разные главы в одну — а это потеря текста,
    которую никто не заметит. Лишний дубль дешевле.
    """
    first, _ = get_or_create_chapter(session, "https://example.com/story?page=1")
    second, created = get_or_create_chapter(session, "https://example.com/story?page=2")

    assert created is True
    assert second.id != first.id


def test_one_book_however_the_link_is_written(session):
    """Раздвоившаяся книга — это два оглавления, и ни одно не полное."""
    get_or_create_chapter(session, "https://www.51shucheng.net/renwen/kniga/1.html")
    get_or_create_chapter(session, "https://51shucheng.net/renwen/kniga/2.html")

    assert count(session, Document) == 1

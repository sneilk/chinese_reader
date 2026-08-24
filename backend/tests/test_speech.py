"""Озвучка перевода: кэш, расходы, отказы.

Провайдер здесь подставной — настоящий SpeechKit стоит денег и требует сети.
Проверяется то, что вокруг него: попадание в кэш, запись расхода **только**
на новый синтез и потолок, который останавливает отправку, а не отчитывается
о ней постфактум.

Ключевая проверка — `test_cache_key_follows_the_text`. Ключ по номеру
предложения выглядел бы естественнее и был бы неверным: после повторного
перевода главы файл остался бы прежним, а текст изменился, и разошлись бы они
молча — заметить это можно только на слух.
"""

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db.base import Base
from app.db.models import Chapter, Document, Sentence, Source, SpeechUsage
from app.providers.speech import SpeechFailure, SpeechResult
from app.services import budget
from app.services.speech import (
    audio_for_sentence,
    cache_path,
    cache_size_bytes,
    prune_cache,
)

pytestmark = pytest.mark.anyio

CONTENT = "Он остановился у двери. Она не обернулась."


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeSynthesizer:
    voice = "alena"
    content_type = "audio/mpeg"
    signature = "fake|alena|neutral|1.0|mp3"

    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.seen: list[str] = []

    async def synthesize(self, text: str) -> SpeechResult:
        self.seen.append(text)
        if self.failure is not None:
            raise self.failure
        return SpeechResult(audio=b"ID3fake-mp3-bytes", content_type=self.content_type,
                            chars_sent=len(text))


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'speech.db'}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, class_=Session, expire_on_commit=False)() as s:
        yield s


@pytest.fixture
def chapter(session) -> Chapter:
    source = Source(kind="web", site="example.com", lang="en")
    document = Document(source=source, key="https://example.com/", lang="en")
    chapter = Chapter(document=document, url="https://example.com/1", lang="en", content=CONTENT)
    chapter.sentences.append(Sentence(idx=0, start_offset=0, end_offset=27))
    session.add(chapter)
    session.commit()
    return chapter


@pytest.fixture
def sentence(session, chapter) -> Sentence:
    first = chapter.sentences[0]
    first.translation = "Он остановился у двери."
    session.commit()
    return first


def usage_rows(session) -> int:
    return int(session.scalar(select(func.count()).select_from(SpeechUsage)) or 0)


# --- кэш ---


async def test_synthesizes_and_writes_file(session, chapter, sentence):
    synth = FakeSynthesizer()

    path, content_type = await audio_for_sentence(session, chapter, sentence, synth)

    assert path.exists()
    assert path.read_bytes() == b"ID3fake-mp3-bytes"
    assert content_type == "audio/mpeg"
    assert synth.seen == ["Он остановился у двери."]


async def test_second_call_hits_the_cache(session, chapter, sentence):
    synth = FakeSynthesizer()
    await audio_for_sentence(session, chapter, sentence, synth)

    await audio_for_sentence(session, chapter, sentence, synth)

    assert len(synth.seen) == 1, "второе прослушивание не должно стоить денег"


async def test_cache_hit_does_not_record_usage(session, chapter, sentence):
    """Расход растёт от синтеза, а не от того, сколько раз это слушали."""
    synth = FakeSynthesizer()
    await audio_for_sentence(session, chapter, sentence, synth)
    await audio_for_sentence(session, chapter, sentence, synth)

    assert usage_rows(session) == 1


async def test_cache_key_follows_the_text(session, chapter, sentence):
    """Перевели главу заново — озвучка обязана стать другой, а не остаться старой."""
    synth = FakeSynthesizer()
    first, _ = await audio_for_sentence(session, chapter, sentence, synth)

    sentence.translation = "Он замер у двери."
    session.commit()
    second, _ = await audio_for_sentence(session, chapter, sentence, synth)

    assert first != second
    assert len(synth.seen) == 2


def test_cache_key_follows_the_voice():
    """Смена голоса обязана дать новый файл, а не отдать старый."""
    one = cache_path("yandex|alena|||mp3", "текст", "audio/mpeg")
    two = cache_path("yandex|filipp|||mp3", "текст", "audio/mpeg")
    assert one != two


def test_cache_is_spread_over_subdirectories():
    """Тысячи файлов в одном каталоге тормозят и `ls`, и бэкап."""
    path = cache_path("sig", "текст", "audio/mpeg")
    assert path.parent.name == path.stem[:2]
    assert path.suffix == ".mp3"


async def test_cache_size_counts_files(session, chapter, sentence):
    assert cache_size_bytes() == 0
    await audio_for_sentence(session, chapter, sentence, FakeSynthesizer())
    assert cache_size_bytes() == len(b"ID3fake-mp3-bytes")


async def test_no_partial_files_left_behind(session, chapter, sentence):
    """Оборванный на середине mp3 остался бы в кэше навсегда и играл бы обрывок."""
    synth = FakeSynthesizer(failure=SpeechFailure("оборвалось"))
    with pytest.raises(SpeechFailure):
        await audio_for_sentence(session, chapter, sentence, synth)

    leftovers = list(settings.tts_cache_dir.rglob("*")) if settings.tts_cache_dir.exists() else []
    assert [f for f in leftovers if f.is_file()] == []


# --- расходы ---


async def test_usage_recorded_with_voice_and_chapter(session, chapter, sentence):
    await audio_for_sentence(session, chapter, sentence, FakeSynthesizer())

    row = session.scalars(select(SpeechUsage)).one()
    assert row.chars_sent == len("Он остановился у двери.")
    assert row.voice == "alena"
    assert row.chapter_id == chapter.id


async def test_month_limit_stops_before_the_network(session, chapter, sentence, monkeypatch):
    monkeypatch.setattr(settings, "speech_max_chars_per_month", 5)
    synth = FakeSynthesizer()

    with pytest.raises(budget.BudgetExceeded):
        await audio_for_sentence(session, chapter, sentence, synth)

    assert synth.seen == [], "при превышении лимита в сеть ходить нельзя"


async def test_speech_budget_is_separate_from_translation(session, chapter, sentence, monkeypatch):
    """Тариф другой: сложенные вместе, оба потолка перестали бы что-то значить."""
    monkeypatch.setattr(settings, "translate_max_chars_per_month", 1)
    budget.record(
        session, provider="yandex", direction="en-ru", chars_sent=100_000, sentences=1
    )

    await audio_for_sentence(session, chapter, sentence, FakeSynthesizer())

    assert budget.speech_chars_this_month(session) == len("Он остановился у двери.")
    assert budget.chars_this_month(session) == 100_000


# --- отказы ---


async def test_sentence_without_translation_refuses(session, chapter):
    """Перевода нет — озвучивать нечего, и это не сбой синтеза."""
    with pytest.raises(SpeechFailure) as e:
        await audio_for_sentence(session, chapter, chapter.sentences[0], FakeSynthesizer())
    assert "перевода" in str(e.value)


async def test_provider_failure_does_not_record_usage(session, chapter, sentence):
    synth = FakeSynthesizer(failure=SpeechFailure("провайдер молчит"))
    with pytest.raises(SpeechFailure):
        await audio_for_sentence(session, chapter, sentence, synth)

    assert usage_rows(session) == 0


# --- потолок кэша ---
#
# Кэш вечный по смыслу, но диск конечен: книга в полтысячи глав озвучивается в
# гигабайты, а рядом на том же диске база и семь её копий. Выброшенный файл при
# этом не потерян — он стоит одного повторного синтеза, и только если к той
# главе вернутся.


def _cached(name: str, size: int, touched: float) -> Path:
    path = settings.tts_cache_dir / name[:2] / f"{name}.mp3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (touched, touched))
    return path


def test_cache_under_the_limit_is_left_alone():
    _cached("aa", 100, 1_000_000)

    assert prune_cache(max_bytes=1000) == (0, 0)
    assert cache_size_bytes() == 100


def test_oldest_go_first():
    old = _cached("aa", 100, 1_000_000)
    new = _cached("bb", 100, 2_000_000)

    removed, freed = prune_cache(max_bytes=150)

    assert (removed, freed) == (1, 100)
    assert not old.exists()
    assert new.exists(), "к недавнему вернутся скорее, чем к позапрошлогоднему"


def test_prunes_until_it_fits():
    for i, stamp in enumerate((1_000_000, 2_000_000, 3_000_000, 4_000_000)):
        _cached(f"{i}{i}", 100, stamp)

    removed, freed = prune_cache(max_bytes=250)

    assert (removed, freed) == (2, 200)
    assert cache_size_bytes() == 200


def test_zero_limit_means_no_pruning():
    """Ноль — это «потолка нет», а не «выбросить всё»: так же читается и бюджет."""
    _cached("aa", 100, 1_000_000)

    assert prune_cache(max_bytes=0) == (0, 0)
    assert cache_size_bytes() == 100


def test_missing_cache_directory_is_not_an_error():
    assert prune_cache(max_bytes=10) == (0, 0)
    assert cache_size_bytes() == 0


async def test_pruned_file_is_synthesized_again(session, chapter, sentence):
    """Выброшенный файл не потерян — он стоит одного повторного синтеза."""
    synth = FakeSynthesizer()
    path, _ = await audio_for_sentence(session, chapter, sentence, synth)
    prune_cache(max_bytes=1)
    assert not path.exists()

    again, _ = await audio_for_sentence(session, chapter, sentence, synth)

    assert again.exists()
    assert len(synth.seen) == 2

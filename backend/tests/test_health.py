"""Живость и пределы, которые сервер объявляет клиенту.

Предел обхода отдаётся отсюда, а не из диагностики, по двум причинам. Первая:
диагностика считает статьи в словаре на три с лишним миллиона строк, и звать её
с экрана ввода ради одного числа значило бы платить за него ожиданием. Вторая:
это единственное число, которое иначе пришлось бы задать на клиенте второй раз,
а разъехавшаяся константа даёт поле, принимающее больше, чем примут в ответ.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.schemas import ChapterCreate
from app.config import settings
from app.main import app

client = TestClient(app)


def test_health_ok():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health_reports_the_walk_limit():
    limits = client.get("/api/health").json()["limits"]
    assert limits["max_chapters_per_run"] == settings.max_chapters_per_run


def test_health_reports_the_whole_book_limit():
    """Выгрузка книги тоже конечна, и клиент должен знать, где именно."""
    limits = client.get("/api/health").json()["limits"]
    assert limits["max_chapters_per_book"] == settings.max_chapters_per_book


def test_announced_limit_is_the_one_actually_enforced():
    """Объявленный предел и принимаемый — одно число, иначе объявление бесполезно.

    Проверяется на схеме, а не через ручку: постановка главы в очередь тянет за
    собой загрузчик и переводчик, а здесь речь про одно число.
    """
    limit = client.get("/api/health").json()["limits"]["max_chapters_per_run"]

    assert ChapterCreate(url="https://example.com/1", follow=limit).follow == limit
    with pytest.raises(ValidationError):
        ChapterCreate(url="https://example.com/1", follow=limit + 1)


def test_unknown_route_404():
    """Отказ маршрутизатора приходит в том же виде, что и наши собственные."""
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert r.json()["error"]["kind"] == "Not Found"

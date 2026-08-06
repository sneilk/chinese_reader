from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_unknown_route_404():
    """Отказ маршрутизатора приходит в том же виде, что и наши собственные."""
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert r.json()["error"]["kind"] == "Not Found"

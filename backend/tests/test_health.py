import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.db import get_db
from app.main import app


class FakeSession:
    def __init__(self, fail=False):
        self.fail = fail

    def execute(self, statement):
        if self.fail:
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))


@pytest.fixture
def client():
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health_ok_when_database_reachable(client):
    app.dependency_overrides[get_db] = lambda: FakeSession()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_503_when_database_unreachable(client):
    app.dependency_overrides[get_db] = lambda: FakeSession(fail=True)

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "unreachable"}


def test_health_allows_frontend_origin(client):
    app.dependency_overrides[get_db] = lambda: FakeSession()

    response = client.get("/health", headers={"Origin": "http://localhost:5173"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"

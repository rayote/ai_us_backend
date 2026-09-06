from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.main import create_app


def test_health_check_returns_environment() -> None:
    settings = Settings(
        environment="test",
        mongodb_uri=None,
        database_name="ai_us_test",
        frontend_origins=(),
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "environment": "test"}

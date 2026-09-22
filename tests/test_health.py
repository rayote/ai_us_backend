from app.core.settings import Settings
from app.main import create_app
from fastapi.testclient import TestClient


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


def test_cors_allows_researcher_patch_requests() -> None:
    frontend_origin = "https://research.example.com"
    settings = Settings(
        environment="test",
        mongodb_uri=None,
        database_name="ai_us_test",
        frontend_origins=(frontend_origin,),
    )

    with TestClient(create_app(settings)) as client:
        response = client.options(
            "/api/v1/researcher/chat-submissions/submission-1/review",
            headers={
                "Origin": frontend_origin,
                "Access-Control-Request-Method": "PATCH",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert response.status_code == 200
    assert "PATCH" in response.headers["access-control-allow-methods"]

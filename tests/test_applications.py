from app.core.settings import Settings
from app.main import create_app
from app.schemas.application import ApplicationCreate
from app.services.applications import ApplicationRepository, DuplicateApplicationError, object_id
from fastapi.testclient import TestClient


class InMemoryApplicationRepository(ApplicationRepository):
    def __init__(self) -> None:
        self._phone_numbers: set[str] = set()

    async def create(self, application: ApplicationCreate) -> str:
        if application.phone in self._phone_numbers:
            raise DuplicateApplicationError
        self._phone_numbers.add(application.phone)
        return object_id()


def _client() -> TestClient:
    settings = Settings("test", None, "ai_us_test", ())
    return TestClient(create_app(settings, InMemoryApplicationRepository()))


def _application_payload() -> dict[str, object]:
    return {
        "gender": "여",
        "grade": "중학교 2학년",
        "phone": "010-1234-5678",
        "guardianPhone": "010-9876-5432",
        "email": "participant@example.com",
        "consents": {
            "documentRead": True,
            "survey": True,
            "chat": False,
            "participant": True,
            "guardian": True,
        },
    }


def test_create_application_returns_pending_status() -> None:
    with _client() as client:
        response = client.post("/api/v1/applications", json=_application_payload())

    assert response.status_code == 201
    assert response.json()["status"] == "pending"
    assert len(response.json()["applicationId"]) == 24


def test_create_application_rejects_duplicate_phone_number() -> None:
    with _client() as client:
        client.post("/api/v1/applications", json=_application_payload())
        response = client.post("/api/v1/applications", json=_application_payload())

    assert response.status_code == 409


def test_create_application_requires_all_required_consents() -> None:
    payload = _application_payload()
    payload["consents"]["guardian"] = False  # type: ignore[index]

    with _client() as client:
        response = client.post("/api/v1/applications", json=payload)

    assert response.status_code == 422

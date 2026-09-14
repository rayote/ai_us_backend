from app.core.settings import Settings
from app.main import create_app
from app.schemas.application import ApplicationCreate
from app.services.applications import ApplicationRepository, DuplicateApplicationError, object_id
from app.services.auth import ParticipantAccount, ParticipantAccountRepository
from fastapi.testclient import TestClient


class InMemoryApplicationRepository(ApplicationRepository):
    def __init__(self) -> None:
        self._phone_numbers: set[str] = set()

    async def create(self, application: ApplicationCreate) -> str:
        if application.phone in self._phone_numbers:
            raise DuplicateApplicationError
        self._phone_numbers.add(application.phone)
        return object_id()


class InMemoryParticipantRepository(ParticipantAccountRepository):
    def __init__(self, existing_phone: str | None = None) -> None:
        self.existing_phone = existing_phone

    async def find_by_phone(self, phone: str) -> ParticipantAccount | None:
        if phone == self.existing_phone:
            return ParticipantAccount("participant-1", phone, "hash", False, False, "초등")
        return None

    async def find_by_id(self, participant_id: str) -> ParticipantAccount | None:
        return None

    async def update_password(self, participant_id: str, password_hash: str) -> bool:
        return False

    async def reset_password_by_phone_email(self, phone: str, email: str, password_hash: str) -> bool:
        return False

    async def create(
        self,
        phone: str,
        password_hash: str,
        chat_consent: bool = False,
        school_level: str | None = None,
        email: str | None = None,
    ) -> bool:
        return False

    async def create_imported(
        self, phone: str, password_hash: str, name: str, school_level: str, grade: int, email: str | None = None
    ) -> bool:
        return False

    async def list_participants(self) -> list[ParticipantAccount]:
        return []


def _client(existing_participant_phone: str | None = None) -> TestClient:
    settings = Settings("test", None, "ai_us_test", ())
    return TestClient(
        create_app(
            settings,
            application_repository=InMemoryApplicationRepository(),
            participant_account_repository=InMemoryParticipantRepository(existing_participant_phone),
        )
    )


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
    assert response.json()["detail"] == "이미 신청된 휴대폰 번호입니다. 연구자 승인 후 로그인해 주세요."


def test_create_application_rejects_registered_participant_phone_number() -> None:
    with _client("01012345678") as client:
        response = client.post("/api/v1/applications", json=_application_payload())

    assert response.status_code == 409
    assert (
        response.json()["detail"]
        == "이미 등록된 참여자 휴대폰 번호입니다. 로그인하거나 비밀번호 찾기를 이용해 주세요."
    )


def test_create_application_requires_all_required_consents() -> None:
    payload = _application_payload()
    payload["consents"]["guardian"] = False  # type: ignore[index]

    with _client() as client:
        response = client.post("/api/v1/applications", json=payload)

    assert response.status_code == 422

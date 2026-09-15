from datetime import UTC, datetime

from app.core.settings import Settings
from app.main import create_app
from app.schemas.application import ApplicationCreate, ApplicationRecord
from app.services.application_settings import ApplicationSettingsRepository
from app.services.applications import ApplicationRepository, DuplicateApplicationError, object_id
from app.services.auth import ParticipantAccount, ParticipantAccountRepository
from fastapi.testclient import TestClient


class InMemoryApplicationRepository(ApplicationRepository):
    def __init__(self) -> None:
        self._phone_numbers: set[str] = set()
        self.records: dict[str, ApplicationRecord] = {}

    async def create(self, application: ApplicationCreate) -> str:
        if application.phone in self._phone_numbers:
            raise DuplicateApplicationError
        self._phone_numbers.add(application.phone)
        application_id = object_id()
        self.records[application_id] = ApplicationRecord(
            applicationId=application_id,
            gender=application.gender,
            grade=application.grade,
            phone=application.phone,
            guardianPhone=application.guardian_phone,
            email=application.email,
            consents=application.consents,
            status="pending",
            submittedAt=datetime.now(UTC),
        )
        return application_id

    async def list_applications(self, school_level: str | None = None) -> list[ApplicationRecord]:
        return list(self.records.values())

    async def get_pending(self, application_ids: list[str]) -> list[ApplicationRecord]:
        return [
            self.records[application_id]
            for application_id in application_ids
            if application_id in self.records and self.records[application_id].status == "pending"
        ]

    async def approve(self, application_ids: list[str]) -> int:
        approved = 0
        for application_id in application_ids:
            record = self.records.get(application_id)
            if record is not None and record.status == "pending":
                self.records[application_id] = record.model_copy(
                    update={"status": "approved", "approved_at": datetime.now(UTC)}
                )
                approved += 1
        return approved


class InMemoryApplicationSettings(ApplicationSettingsRepository):
    def __init__(self, auto_approval: bool = False) -> None:
        self.auto_approval = auto_approval

    async def auto_approval_enabled(self) -> bool:
        return self.auto_approval

    async def set_auto_approval(self, enabled: bool, updated_by: str) -> bool:
        self.auto_approval = enabled
        return enabled


class InMemoryParticipantRepository(ParticipantAccountRepository):
    def __init__(self, existing_phone: str | None = None) -> None:
        self.accounts: dict[str, ParticipantAccount] = {}
        if existing_phone is not None:
            self.accounts[existing_phone] = ParticipantAccount("participant-1", existing_phone, "hash", False, False, "초등")

    async def find_by_phone(self, phone: str) -> ParticipantAccount | None:
        return self.accounts.get(phone)

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
        if phone in self.accounts:
            return False
        self.accounts[phone] = ParticipantAccount(
            f"participant-{len(self.accounts) + 1}", phone, password_hash, True, chat_consent, school_level, email=email
        )
        return True

    async def create_imported(
        self, phone: str, password_hash: str, name: str, school_level: str, grade: int, email: str | None = None
    ) -> bool:
        return False

    async def list_participants(self) -> list[ParticipantAccount]:
        return []


def _client(existing_participant_phone: str | None = None, auto_approval: bool = False) -> TestClient:
    settings = Settings("test", None, "ai_us_test", (), "test-secret-at-least-thirty-two-bytes")
    return TestClient(
        create_app(
            settings,
            application_repository=InMemoryApplicationRepository(),
            application_settings_repository=InMemoryApplicationSettings(auto_approval),
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


def test_create_application_auto_approves_and_returns_password_change_login() -> None:
    with _client(auto_approval=True) as client:
        response = client.post("/api/v1/applications", json=_application_payload())

    assert response.status_code == 201
    assert response.json()["status"] == "approved"
    assert response.json()["accessToken"]
    assert response.json()["needsPasswordChange"] is True
    assert response.json()["audience"] == "secondary"


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

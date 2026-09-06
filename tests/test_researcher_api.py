import asyncio
from datetime import UTC, datetime

from app.core.security import hash_password
from app.core.settings import Settings
from app.main import create_app
from app.schemas.application import ApplicationConsents, ApplicationCreate, ApplicationRecord
from app.services.applications import ApplicationRepository
from app.services.auth import (
    ParticipantAccount,
    ParticipantAccountRepository,
    ResearcherAccount,
    ResearcherAccountRepository,
)
from fastapi.testclient import TestClient


class InMemoryApplications(ApplicationRepository):
    def __init__(self) -> None:
        self.records = [
            ApplicationRecord(
                applicationId="65f000000000000000000001",
                gender="여",
                grade="초등학교 5학년",
                phone="01012345678",
                guardianPhone="01099999999",
                email="participant@example.com",
                consents=ApplicationConsents(
                    documentRead=True, survey=True, chat=False, participant=True, guardian=True
                ),
                status="pending",
                submittedAt=datetime.now(UTC),
            )
        ]

    async def create(self, application: ApplicationCreate) -> str:
        raise NotImplementedError

    async def list_applications(self, school_level: str | None = None) -> list[ApplicationRecord]:
        return self.records

    async def get_pending(self, application_ids: list[str]) -> list[ApplicationRecord]:
        return [
            record
            for record in self.records
            if record.application_id in application_ids and record.status == "pending"
        ]

    async def approve(self, application_ids: list[str]) -> int:
        approved = 0
        for index, record in enumerate(self.records):
            if record.application_id in application_ids and record.status == "pending":
                self.records[index] = record.model_copy(
                    update={"status": "approved", "approved_at": datetime.now(UTC)}
                )
                approved += 1
        return approved


class InMemoryParticipants(ParticipantAccountRepository):
    def __init__(self) -> None:
        self.accounts: dict[str, ParticipantAccount] = {}

    async def find_by_phone(self, phone: str) -> ParticipantAccount | None:
        return self.accounts.get(phone)

    async def find_by_id(self, participant_id: str) -> ParticipantAccount | None:
        return next((account for account in self.accounts.values() if account.participant_id == participant_id), None)

    async def update_password(self, participant_id: str, password_hash: str) -> bool:
        return False

    async def create(self, phone: str, password_hash: str) -> bool:
        if phone in self.accounts:
            return False
        self.accounts[phone] = ParticipantAccount(phone, phone, password_hash, True)
        return True

    async def create_imported(
        self, phone: str, password_hash: str, name: str, school_level: str, grade: int
    ) -> bool:
        return await self.create(phone, password_hash)


class InMemoryResearchers(ResearcherAccountRepository):
    def __init__(self) -> None:
        self.accounts = {
            "admin": ResearcherAccount("admin-1", "admin", hash_password("admin-password"), "admin"),
            "researcher": ResearcherAccount(
                "researcher-1", "researcher", hash_password("researcher-password"), "researcher"
            ),
        }

    async def find_by_username(self, username: str) -> ResearcherAccount | None:
        return self.accounts.get(username)

    async def ensure_bootstrap(self, username: str, password_hash: str) -> None:
        return None

    async def create(self, username: str, password_hash: str, role: str) -> str | None:
        if username in self.accounts:
            return None
        researcher_id = f"researcher-{len(self.accounts)}"
        self.accounts[username] = ResearcherAccount(researcher_id, username, password_hash, role)
        return researcher_id


def _client() -> tuple[TestClient, InMemoryParticipants]:
    participants = InMemoryParticipants()
    return (
        TestClient(
            create_app(
                Settings("test", None, "ai_us_test", (), "test-secret-at-least-thirty-two-bytes", 60),
                application_repository=InMemoryApplications(),
                participant_account_repository=participants,
                researcher_account_repository=InMemoryResearchers(),
            )
        ),
        participants,
    )


def _researcher_token(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/researcher/login",
        json={"username": "researcher", "password": "researcher-password"},
    )
    return response.json()["accessToken"]


def _admin_token(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/researcher/login",
        json={"username": "admin", "password": "admin-password"},
    )
    return response.json()["accessToken"]


def test_researcher_can_list_and_approve_applications() -> None:
    client, participants = _client()
    with client:
        token = _researcher_token(client)
        headers = {"Authorization": f"Bearer {token}"}
        list_response = client.get("/api/v1/researcher/applications", headers=headers)
        approval_response = client.post(
            "/api/v1/researcher/applications/approve",
            headers=headers,
            json={"applicationIds": ["65f000000000000000000001"]},
        )

    assert list_response.status_code == 200
    assert len(list_response.json()) == 1
    assert approval_response.json() == {"approvedCount": 1}
    assert participants.accounts["01012345678"].must_change_password is True


def test_participant_cannot_access_researcher_applications() -> None:
    client, _ = _client()
    with client:
        response = client.get("/api/v1/researcher/applications")

    assert response.status_code == 401


def test_admin_can_create_researcher_but_researcher_cannot() -> None:
    client, _ = _client()
    with client:
        admin_response = client.post(
            "/api/v1/admin/researchers",
            headers={"Authorization": f"Bearer {_admin_token(client)}"},
            json={"username": "assistant", "password": "assistant-password"},
        )
        researcher_response = client.post(
            "/api/v1/admin/researchers",
            headers={"Authorization": f"Bearer {_researcher_token(client)}"},
            json={"username": "blocked", "password": "blocked-password"},
        )

    assert admin_response.status_code == 201
    assert admin_response.json()["role"] == "researcher"
    assert researcher_response.status_code == 403


def test_admin_can_access_researcher_application_management() -> None:
    client, _ = _client()
    with client:
        response = client.get(
            "/api/v1/researcher/applications",
            headers={"Authorization": f"Bearer {_admin_token(client)}"},
        )

    assert response.status_code == 200


def test_researcher_can_import_participants_from_csv() -> None:
    client, participants = _client()
    with client:
        response = client.post(
            "/api/v1/researcher/participants/imports",
            headers={"Authorization": f"Bearer {_researcher_token(client)}"},
            files={"file": ("participants.csv", "이름,휴대폰번호,학교급,학년\n홍길동,010-1234-5678,초등,4\n", "text/csv")},
        )

    assert response.json() == {"createdCount": 1, "skippedCount": 0, "errors": []}
    assert "01012345678" in participants.accounts

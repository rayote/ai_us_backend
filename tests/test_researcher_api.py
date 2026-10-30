import asyncio
from dataclasses import replace
from datetime import UTC, datetime

from app.core.security import hash_password
from app.core.settings import Settings
from app.main import create_app
from app.schemas.application import (
    ApplicationConsents,
    ApplicationCreate,
    ApplicationRecord,
)
from app.services.application_settings import ApplicationSettingsRepository
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
                    documentRead=True,
                    survey=True,
                    chat=False,
                    participant=True,
                    guardian=True,
                ),
                status="pending",
                submittedAt=datetime.now(UTC),
            )
        ]

    async def create(self, application: ApplicationCreate) -> str:
        raise NotImplementedError

    async def list_applications(
        self, school_level: str | None = None
    ) -> list[ApplicationRecord]:
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

    async def set_chat_consent(self, application_id: str, enabled: bool) -> bool:
        for index, record in enumerate(self.records):
            if record.application_id == application_id:
                self.records[index] = record.model_copy(
                    update={
                        "consents": record.consents.model_copy(update={"chat": enabled})
                    }
                )
                return True
        return False


class InMemoryApplicationSettings(ApplicationSettingsRepository):
    def __init__(self) -> None:
        self.auto_approval = False

    async def auto_approval_enabled(self) -> bool:
        return self.auto_approval

    async def set_auto_approval(self, enabled: bool, updated_by: str) -> bool:
        self.auto_approval = enabled
        return enabled


class InMemoryParticipants(ParticipantAccountRepository):
    def __init__(self) -> None:
        self.accounts: dict[str, ParticipantAccount] = {}

    async def find_by_phone(self, phone: str) -> ParticipantAccount | None:
        return self.accounts.get(phone)

    async def find_by_id(self, participant_id: str) -> ParticipantAccount | None:
        return next(
            (
                account
                for account in self.accounts.values()
                if account.participant_id == participant_id
            ),
            None,
        )

    async def change_chat_consent(self, account, enabled, restore_existing, reason, actor):
        current = self.accounts[account.phone]
        if current.chat_consent_revision != account.chat_consent_revision:
            return False
        cutoff = datetime.now(UTC) if not enabled else (None if restore_existing else account.chat_restricted_before)
        self.accounts[account.phone] = replace(
            current, chat_consent=enabled, chat_restricted_before=cutoff,
            chat_consent_revision=current.chat_consent_revision + 1,
            chat_consent_history=current.chat_consent_history + ({
                "previousConsent": current.chat_consent, "chatConsent": enabled,
                "restoreExisting": restore_existing, "reason": reason,
                "updatedAt": datetime.now(UTC), "updatedBy": actor,
            },),
        )
        return True

    async def update_password(self, participant_id: str, password_hash: str) -> bool:
        return False

    async def set_chat_consent(self, participant_id: str, enabled: bool) -> bool:
        for phone, account in self.accounts.items():
            if account.participant_id == participant_id:
                self.accounts[phone] = account.__class__(
                    account.participant_id,
                    account.phone,
                    account.password_hash,
                    account.must_change_password,
                    enabled,
                    account.school_level,
                    account.name,
                    account.grade,
                    account.email,
                    account.guardian_phone,
                    account.sns,
                )
                return True
        return False

    async def reset_password_by_phone_email(
        self, phone: str, email: str, password_hash: str
    ) -> bool:
        return False

    async def create(
        self,
        phone: str,
        password_hash: str,
        chat_consent: bool = False,
        school_level: str | None = None,
        email: str | None = None,
        guardian_phone: str | None = None,
        sns: str | None = None,
    ) -> bool:
        if phone in self.accounts:
            return False
        self.accounts[phone] = ParticipantAccount(
            phone,
            phone,
            password_hash,
            True,
            chat_consent,
            school_level,
            email=email,
            guardian_phone=guardian_phone,
            sns=sns,
        )
        return True

    async def create_imported(
        self,
        phone: str,
        password_hash: str,
        name: str,
        school_level: str,
        grade: int,
        email: str | None = None,
    ) -> bool:
        return await self.create(
            phone, password_hash, school_level=school_level, email=email
        )


class InMemoryResearchers(ResearcherAccountRepository):
    def __init__(self) -> None:
        self.accounts = {
            "admin": ResearcherAccount(
                "admin-1", "admin", hash_password("admin-password"), "admin"
            ),
            "researcher": ResearcherAccount(
                "researcher-1",
                "researcher",
                hash_password("researcher-password"),
                "researcher",
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
        self.accounts[username] = ResearcherAccount(
            researcher_id, username, password_hash, role
        )
        return researcher_id


def _client() -> tuple[TestClient, InMemoryParticipants]:
    participants = InMemoryParticipants()
    return (
        TestClient(
            create_app(
                Settings(
                    "test",
                    None,
                    "ai_us_test",
                    (),
                    "test-secret-at-least-thirty-two-bytes",
                    60,
                ),
                application_repository=InMemoryApplications(),
                application_settings_repository=InMemoryApplicationSettings(),
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
    assert participants.accounts["01012345678"].chat_consent is False
    assert participants.accounts["01012345678"].email == "participant@example.com"
    assert participants.accounts["01012345678"].guardian_phone == "01099999999"


def test_researcher_chat_consent_update_preserves_original_application() -> None:
    client, participants = _client()
    with client:
        token = _researcher_token(client)
        approval_response = client.post(
            "/api/v1/researcher/applications/approve",
            headers={"Authorization": f"Bearer {token}"},
            json={"applicationIds": ["65f000000000000000000001"]},
        )
        assert approval_response.status_code == 200
        participants.accounts["01012345678"] = ParticipantAccount(
            "01012345678",
            "01012345678",
            "hash",
            False,
            False,
            "초등",
            guardian_phone="01099999999",
        )
        response = client.put(
            "/api/v1/researcher/applications/65f000000000000000000001/chat-consent",
            headers={"Authorization": f"Bearer {token}"},
            json={"chatConsent": True, "expectedRevision": 0, "reason": "본인 요청"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "applicationId": "65f000000000000000000001",
        "chatConsent": True,
    }
    assert participants.accounts["01012345678"].chat_consent is True
    assert client.app.state.application_repository.records[0].consents.chat is False
    assert participants.accounts["01012345678"].chat_consent_history[0]["reason"] == "본인 요청"


def test_csv_account_consent_lookup_withdrawal_and_explicit_restoration() -> None:
    client, participants = _client()
    participants.accounts["01012345678"] = ParticipantAccount(
        "csv-account", "01012345678", "hash", False, True, "초등",
    )
    with client:
        client.app.state.application_repository.records = []
        headers = {"Authorization": f"Bearer {_researcher_token(client)}"}
        lookup = client.post("/api/v1/researcher/participants/chat-consent/lookup", headers=headers, json={"phone": "01012345678"})
        assert lookup.status_code == 200
        assert lookup.json()["participantId"] == "csv-account"
        path = "/api/v1/researcher/participants/csv-account/chat-consent"
        withdrawn = client.put(path, headers=headers, json={"chatConsent": False, "expectedRevision": 0, "reason": "연락으로 철회"})
        assert withdrawn.status_code == 200
        assert withdrawn.json()["restrictedBefore"]
        stale = client.put(path, headers=headers, json={"chatConsent": True, "expectedRevision": 0, "reason": "오래된 화면"})
        assert stale.status_code == 409
        future = client.put(path, headers=headers, json={"chatConsent": True, "expectedRevision": 1, "reason": "향후 제출만 재동의"})
        assert future.status_code == 200
        assert future.json()["restrictedBefore"] == withdrawn.json()["restrictedBefore"]
        restored = client.put(path, headers=headers, json={"chatConsent": True, "restoreExisting": True, "expectedRevision": 2, "reason": "기존 자료 복원 요청"})
        assert restored.status_code == 200
        assert restored.json()["restrictedBefore"] is None
        assert len(restored.json()["history"]) == 3
        assert len(client.app.state.application_repository.records) == 0
        assert client.put(path, headers=headers, json={"chatConsent": False, "restoreExisting": True, "expectedRevision": 3, "reason": "invalid"}).status_code == 422
        assert client.put(path, headers=headers, json={"chatConsent": False, "expectedRevision": 3, "reason": " "}).status_code == 422
        assert client.post("/api/v1/researcher/participants/chat-consent/lookup", json={"phone": "01012345678"}).status_code == 401


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


def test_researcher_can_view_but_only_admin_can_change_auto_approval() -> None:
    client, _ = _client()
    with client:
        researcher_headers = {"Authorization": f"Bearer {_researcher_token(client)}"}
        admin_headers = {"Authorization": f"Bearer {_admin_token(client)}"}
        initial_response = client.get(
            "/api/v1/researcher/application-settings", headers=researcher_headers
        )
        update_response = client.put(
            "/api/v1/admin/application-settings",
            headers=admin_headers,
            json={"autoApproval": True},
        )
        refreshed_response = client.get(
            "/api/v1/researcher/application-settings", headers=researcher_headers
        )
        blocked_response = client.put(
            "/api/v1/admin/application-settings",
            headers=researcher_headers,
            json={"autoApproval": False},
        )

    assert initial_response.json() == {"autoApproval": False}
    assert update_response.json() == {"autoApproval": True}
    assert refreshed_response.json() == {"autoApproval": True}
    assert blocked_response.status_code == 403


def test_researcher_cannot_approve_application_for_existing_participant() -> None:
    client, participants = _client()
    asyncio.run(
        participants.create("01012345678", hash_password("1234"), school_level="초등")
    )
    with client:
        response = client.post(
            "/api/v1/researcher/applications/approve",
            headers={"Authorization": f"Bearer {_researcher_token(client)}"},
            json={"applicationIds": ["65f000000000000000000001"]},
        )

    assert response.status_code == 409
    assert "이미 등록된 참여자" in response.json()["detail"]


def test_approval_rechecks_guardian_against_newly_registered_participant() -> None:
    client, participants = _client()
    asyncio.run(
        participants.create("01099999999", hash_password("1234"), school_level="초등")
    )
    with client:
        response = client.post(
            "/api/v1/researcher/applications/approve",
            headers={"Authorization": f"Bearer {_researcher_token(client)}"},
            json={"applicationIds": ["65f000000000000000000001"]},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "이미 가입된 사용자 번호는 보호자 번호로 입력할 수 없어요. 연락처를 다시 확인하세요."
    )
    assert "01012345678" not in participants.accounts
    assert client.app.state.application_repository.records[0].status == "pending"


def test_researcher_can_import_participants_from_csv() -> None:
    client, participants = _client()
    with client:
        response = client.post(
            "/api/v1/researcher/participants/imports",
            headers={"Authorization": f"Bearer {_researcher_token(client)}"},
            files={
                "file": (
                    "participants.csv",
                    "이름,휴대폰번호,보호자휴대폰,학교급,학년,이메일\n홍길동,010-1234-5678,010-9999-9999,초등,4,user@example.com\n",
                    "text/csv",
                )
            },
        )

    assert response.json() == {"createdCount": 1, "skippedCount": 0, "errors": []}
    assert "01012345678" in participants.accounts
    assert participants.accounts["01012345678"].email == "user@example.com"

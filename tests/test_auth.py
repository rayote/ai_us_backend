from datetime import UTC, datetime

from app.core.security import hash_password, verify_password
from app.core.settings import Settings
from app.main import create_app
from app.services.auth import ParticipantAccount, ParticipantAccountRepository
from fastapi.testclient import TestClient


class InMemoryParticipantAccountRepository(ParticipantAccountRepository):
    def __init__(self) -> None:
        self.account = ParticipantAccount(
            "participant-1",
            "01012345678",
            hash_password("changed-password"),
            False,
            True,
            "초등",
            email="participant@example.com",
        )

    async def find_by_phone(self, phone: str) -> ParticipantAccount | None:
        if phone == self.account.phone:
            return self.account
        return None

    async def find_by_id(self, participant_id: str) -> ParticipantAccount | None:
        if participant_id == self.account.participant_id:
            return self.account
        return None

    async def update_password(self, participant_id: str, password_hash: str) -> bool:
        if participant_id != self.account.participant_id:
            return False
        self.account = ParticipantAccount(
            participant_id,
            self.account.phone,
            password_hash,
            False,
            self.account.chat_consent,
            "초등",
            email=self.account.email,
        )
        return True

    async def reset_password_by_phone_email(self, phone: str, email: str, password_hash: str) -> bool:
        if phone != self.account.phone or email != self.account.email:
            return False
        self.account = ParticipantAccount(
            self.account.participant_id,
            self.account.phone,
            password_hash,
            True,
            self.account.chat_consent,
            self.account.school_level,
            email=self.account.email,
        )
        return True


def _client() -> TestClient:
    settings = Settings("test", None, "ai_us_test", (), "test-secret-at-least-thirty-two-bytes", 60)
    return TestClient(create_app(settings, participant_account_repository=InMemoryParticipantAccountRepository()))


def test_participant_login_returns_access_token_and_password_change_requirement() -> None:
    with _client() as client:
        response = client.post(
            "/api/v1/auth/participant/login",
            json={"phone": "010-1234-5678", "password": "changed-password", "audience": "elementary"},
        )

    assert response.status_code == 200
    assert response.json()["role"] == "participant"
    assert response.json()["needsPasswordChange"] is False
    assert response.json()["audience"] == "elementary"
    assert response.json()["chatConsent"] is True
    assert response.json()["accessToken"]


def test_participant_changes_default_password() -> None:
    with _client() as client:
        login_response = client.post(
            "/api/v1/auth/participant/login",
            json={"phone": "01012345678", "password": "changed-password", "audience": "elementary"},
        )
        token = login_response.json()["accessToken"]
        response = client.post(
            "/api/v1/auth/participant/password",
            headers={"Authorization": f"Bearer {token}"},
            json={"currentPassword": "changed-password", "newPassword": "new-password-2026"},
        )
        new_login_response = client.post(
            "/api/v1/auth/participant/login",
            json={"phone": "01012345678", "password": "new-password-2026", "audience": "elementary"},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "completed"}
    assert new_login_response.json()["needsPasswordChange"] is False


def test_participant_login_rejects_invalid_password() -> None:
    with _client() as client:
        response = client.post(
            "/api/v1/auth/participant/login",
            json={"phone": "01012345678", "password": "wrong", "audience": "elementary"},
        )

    assert response.status_code == 401


def test_participant_login_rejects_wrong_school_audience() -> None:
    with _client() as client:
        response = client.post(
            "/api/v1/auth/participant/login",
            json={"phone": "01012345678", "password": "changed-password", "audience": "secondary"},
        )

    assert response.status_code == 403


def test_participant_password_reset_restores_default_password_and_requires_change() -> None:
    with _client() as client:
        response = client.post(
            "/api/v1/auth/participant/password-reset",
            json={"phone": "010-1234-5678", "email": "PARTICIPANT@example.com"},
        )
        login = client.post(
            "/api/v1/auth/participant/login",
            json={"phone": "01012345678", "password": "1234", "audience": "elementary"},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "completed"}
    assert login.status_code == 200
    assert login.json()["needsPasswordChange"] is True


def test_participant_password_reset_rejects_unmatched_email() -> None:
    with _client() as client:
        response = client.post(
            "/api/v1/auth/participant/password-reset",
            json={"phone": "01012345678", "email": "other@example.com"},
        )

    assert response.status_code == 404

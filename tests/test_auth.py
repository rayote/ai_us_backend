from datetime import UTC, datetime

from app.core.security import hash_password
from app.core.settings import Settings
from app.main import create_app
from app.services.auth import ParticipantAccount, ParticipantAccountRepository
from fastapi.testclient import TestClient


class InMemoryParticipantAccountRepository(ParticipantAccountRepository):
    def __init__(self) -> None:
        self.account = ParticipantAccount("participant-1", "01012345678", hash_password("1234"), True)

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
        self.account = ParticipantAccount(participant_id, self.account.phone, password_hash, False)
        return True


def _client() -> TestClient:
    settings = Settings("test", None, "ai_us_test", (), "test-secret-at-least-thirty-two-bytes", 60)
    return TestClient(create_app(settings, participant_account_repository=InMemoryParticipantAccountRepository()))


def test_participant_login_returns_access_token_and_password_change_requirement() -> None:
    with _client() as client:
        response = client.post("/api/v1/auth/participant/login", json={"phone": "010-1234-5678", "password": "1234"})

    assert response.status_code == 200
    assert response.json()["role"] == "participant"
    assert response.json()["needsPasswordChange"] is True
    assert response.json()["accessToken"]


def test_participant_changes_default_password() -> None:
    with _client() as client:
        login_response = client.post(
            "/api/v1/auth/participant/login", json={"phone": "01012345678", "password": "1234"}
        )
        token = login_response.json()["accessToken"]
        response = client.post(
            "/api/v1/auth/participant/password",
            headers={"Authorization": f"Bearer {token}"},
            json={"currentPassword": "1234", "newPassword": "new-password-2026"},
        )
        new_login_response = client.post(
            "/api/v1/auth/participant/login",
            json={"phone": "01012345678", "password": "new-password-2026"},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "completed"}
    assert new_login_response.json()["needsPasswordChange"] is False


def test_participant_login_rejects_invalid_password() -> None:
    with _client() as client:
        response = client.post("/api/v1/auth/participant/login", json={"phone": "01012345678", "password": "wrong"})

    assert response.status_code == 401

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from app.core.security import create_access_token
from app.core.settings import Settings
from app.main import create_app
from app.services.worker_health import worker_health_snapshot
from app.worker import acquire_worker_process_lock, maintain_worker_heartbeat
from fastapi.testclient import TestClient


class InMemoryWorkerHealth:
    def __init__(self, heartbeat: datetime | None) -> None:
        self.heartbeat = heartbeat

    async def touch(self, timestamp: datetime) -> None:
        self.heartbeat = timestamp

    async def last_heartbeat(self) -> datetime | None:
        return self.heartbeat


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


def test_public_health_check_does_not_expose_worker_status() -> None:
    settings = Settings("test", None, "ai_us_test", ())
    repository = InMemoryWorkerHealth(datetime.now(UTC))

    with TestClient(
        create_app(settings, worker_health_repository=repository)
    ) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "environment": "test"}


def test_worker_health_snapshot_reports_fresh_and_stale_heartbeats() -> None:
    now = datetime.now(UTC)
    fresh = asyncio.run(
        worker_health_snapshot(InMemoryWorkerHealth(now - timedelta(seconds=30)), now)
    )
    stale = asyncio.run(
        worker_health_snapshot(InMemoryWorkerHealth(now - timedelta(seconds=61)), now)
    )

    assert fresh["status"] == "ok"
    assert fresh["ageSeconds"] == 30.0
    assert stale["status"] == "stale"
    assert stale["ageSeconds"] == 61.0


def test_researcher_system_health_requires_authentication_and_reports_worker() -> None:
    settings = Settings(
        "test",
        None,
        "ai_us_test",
        (),
        jwt_secret="test-secret-that-is-at-least-32-characters-long",
    )
    repository = InMemoryWorkerHealth(datetime.now(UTC))
    token = create_access_token(
        "researcher-1",
        "researcher",
        settings.jwt_secret,
        settings.jwt_expiration_minutes,
    )

    with TestClient(
        create_app(settings, worker_health_repository=repository)
    ) as client:
        unauthorized = client.get("/api/v1/researcher/system-health")
        response = client.get(
            "/api/v1/researcher/system-health",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.json()["api"]["status"] == "ok"
    assert response.json()["worker"]["status"] == "ok"


def test_worker_heartbeat_is_published_immediately() -> None:
    repository = InMemoryWorkerHealth(None)

    async def publish() -> None:
        task = asyncio.create_task(maintain_worker_heartbeat(repository))
        await asyncio.sleep(0)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(publish())

    assert repository.heartbeat is not None
    assert datetime.now(UTC) - repository.heartbeat < timedelta(seconds=1)


def test_worker_process_lock_prevents_duplicate_processes(tmp_path) -> None:
    lock_path = str(tmp_path / "worker.lock")
    first_lock = acquire_worker_process_lock(lock_path)
    try:
        try:
            acquire_worker_process_lock(lock_path)
        except RuntimeError as error:
            assert str(error) == "Another worker process is already running."
        else:
            raise AssertionError("A duplicate worker acquired the process lock")
    finally:
        first_lock.close()

    replacement_lock = acquire_worker_process_lock(lock_path)
    replacement_lock.close()


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

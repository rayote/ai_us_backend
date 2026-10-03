from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

WORKER_HEARTBEAT_ID = "queue-worker"
WORKER_HEARTBEAT_STALE_SECONDS = 60


class WorkerHealthRepository(Protocol):
    async def touch(self, timestamp: datetime) -> None: ...

    async def last_heartbeat(self) -> datetime | None: ...


class MongoWorkerHealthRepository:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def touch(self, timestamp: datetime) -> None:
        await self._collection.update_one(
            {"_id": WORKER_HEARTBEAT_ID},
            {"$set": {"updated_at": timestamp}},
            upsert=True,
        )

    async def last_heartbeat(self) -> datetime | None:
        document = await self._collection.find_one({"_id": WORKER_HEARTBEAT_ID})
        if document is None:
            return None
        timestamp = document.get("updated_at")
        if not isinstance(timestamp, datetime):
            return None
        return timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp


async def worker_health_snapshot(
    repository: WorkerHealthRepository | None,
    now: datetime | None = None,
) -> dict[str, object]:
    checked_at = now or datetime.now(UTC)
    if repository is None:
        return {"status": "unavailable", "lastHeartbeatAt": None}
    try:
        last_heartbeat = await repository.last_heartbeat()
    except Exception:
        return {"status": "unavailable", "lastHeartbeatAt": None}
    if last_heartbeat is None:
        return {"status": "missing", "lastHeartbeatAt": None}

    heartbeat_age = max(0.0, (checked_at - last_heartbeat).total_seconds())
    return {
        "status": (
            "ok" if heartbeat_age <= WORKER_HEARTBEAT_STALE_SECONDS else "stale"
        ),
        "lastHeartbeatAt": last_heartbeat.isoformat(),
        "ageSeconds": round(heartbeat_age, 1),
    }

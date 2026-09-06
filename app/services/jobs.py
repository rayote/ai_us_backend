from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from bson import ObjectId
from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.schemas.jobs import Job, JobCreate

JobHandler = Callable[[dict[str, Any]], Awaitable[None]]


class JobRepository(Protocol):
    async def enqueue(self, job: JobCreate) -> Job: ...

    async def recover_interrupted(self) -> int: ...

    async def claim_next(self) -> Job | None: ...

    async def complete(self, job_id: str) -> None: ...

    async def retry(self, job_id: str, error: str) -> None: ...

    async def fail(self, job_id: str, error: str) -> None: ...


def _job_from_document(document: dict[str, Any]) -> Job:
    return Job(
        id=str(document["_id"]),
        job_type=document["job_type"],
        idempotency_key=document["idempotency_key"],
        payload=document["payload"],
        status=document["status"],
        attempts=document["attempts"],
        created_at=document["created_at"],
        processed_at=document.get("processed_at"),
        error=document.get("error"),
    )


class MongoJobRepository:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def enqueue(self, job: JobCreate) -> Job:
        document = {
            "job_type": job.job_type,
            "idempotency_key": job.idempotency_key,
            "payload": job.payload,
            "status": "queued",
            "attempts": 0,
            "created_at": datetime.now(UTC),
            "processed_at": None,
            "error": None,
        }
        try:
            result = await self._collection.insert_one(document)
            document["_id"] = result.inserted_id
        except DuplicateKeyError:
            document = await self._collection.find_one(
                {"job_type": job.job_type, "idempotency_key": job.idempotency_key}
            )
            if document is None:
                raise
        return _job_from_document(document)

    async def claim_next(self) -> Job | None:
        document = await self._collection.find_one_and_update(
            {"status": "queued"},
            {
                "$set": {"status": "processing", "error": None},
                "$inc": {"attempts": 1},
            },
            sort=[("created_at", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        return _job_from_document(document) if document else None

    async def recover_interrupted(self) -> int:
        result = await self._collection.update_many(
            {"status": "processing"},
            {
                "$set": {
                    "status": "queued",
                    "error": "Worker restarted before this job completed.",
                }
            },
        )
        return result.modified_count

    async def complete(self, job_id: str) -> None:
        await self._collection.update_one(
            {"_id": ObjectId(job_id), "status": "processing"},
            {"$set": {"status": "completed", "processed_at": datetime.now(UTC)}},
        )

    async def retry(self, job_id: str, error: str) -> None:
        await self._collection.update_one(
            {"_id": ObjectId(job_id), "status": "processing"},
            {"$set": {"status": "queued", "error": error[:1000]}},
        )

    async def fail(self, job_id: str, error: str) -> None:
        await self._collection.update_one(
            {"_id": ObjectId(job_id), "status": "processing"},
            {
                "$set": {
                    "status": "failed",
                    "processed_at": datetime.now(UTC),
                    "error": error[:1000],
                }
            },
        )


class QueueWorker:
    def __init__(
        self,
        repository: JobRepository,
        handlers: dict[str, JobHandler],
        max_attempts: int = 3,
    ) -> None:
        self._repository = repository
        self._handlers = handlers
        self._max_attempts = max_attempts

    async def recover(self) -> int:
        return await self._repository.recover_interrupted()

    async def process_one(self) -> bool:
        job = await self._repository.claim_next()
        if job is None:
            return False

        handler = self._handlers.get(job.job_type)
        if handler is None:
            await self._repository.fail(job.id, f"No handler registered for {job.job_type}")
            return True

        try:
            await handler(job.payload)
        except Exception as error:
            if job.attempts < self._max_attempts:
                await self._repository.retry(job.id, str(error))
            else:
                await self._repository.fail(job.id, str(error))
        else:
            await self._repository.complete(job.id)
        return True
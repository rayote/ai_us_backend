from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from app.schemas.application import ApplicationCreate, ApplicationRecord
from bson import ObjectId
from pymongo.errors import DuplicateKeyError


class DuplicateApplicationError(Exception):
    """Raised when a participant phone number already has an application."""


class ApplicationRepository(Protocol):
    async def create(self, application: ApplicationCreate) -> str: ...

    async def list_applications(self, school_level: str | None = None) -> list[ApplicationRecord]: ...

    async def get_pending(self, application_ids: list[str]) -> list[ApplicationRecord]: ...

    async def approve(self, application_ids: list[str]) -> int: ...


_SCHOOL_LEVEL_PATTERNS = {
    "elementary": "^초등",
    "middle": "^중학",
    "high": "^고등",
}


def _application_record(document: dict[str, Any]) -> ApplicationRecord:
    return ApplicationRecord(
        applicationId=str(document["_id"]),
        gender=document["gender"],
        grade=document["grade"],
        phone=document["phone"],
        guardianPhone=document["guardian_phone"],
        email=document["email"],
        consents=document["consents"],
        status=document["status"],
        submittedAt=document["submitted_at"],
        approvedAt=document.get("approved_at"),
    )


class MongoApplicationRepository:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def create(self, application: ApplicationCreate) -> str:
        document = application.model_dump()
        document.update(
            {
                "phone_normalized": application.phone,
                "status": "pending",
                "submitted_at": datetime.now(UTC),
            }
        )

        try:
            result = await self._collection.insert_one(document)
        except DuplicateKeyError as error:
            raise DuplicateApplicationError from error

        return str(result.inserted_id)

    async def list_applications(self, school_level: str | None = None) -> list[ApplicationRecord]:
        filters: dict[str, Any] = {}
        if school_level is not None:
            filters["grade"] = {"$regex": _SCHOOL_LEVEL_PATTERNS[school_level]}

        cursor = self._collection.find(filters).sort("submitted_at", -1)
        return [_application_record(document) async for document in cursor]

    async def approve(self, application_ids: list[str]) -> int:
        object_ids = [ObjectId(application_id) for application_id in application_ids]
        result = await self._collection.update_many(
            {"_id": {"$in": object_ids}, "status": "pending"},
            {"$set": {"status": "approved", "approved_at": datetime.now(UTC)}},
        )
        return result.modified_count

    async def get_pending(self, application_ids: list[str]) -> list[ApplicationRecord]:
        object_ids = [ObjectId(application_id) for application_id in application_ids]
        cursor = self._collection.find({"_id": {"$in": object_ids}, "status": "pending"})
        return [_application_record(document) async for document in cursor]


def object_id() -> str:
    return str(ObjectId())

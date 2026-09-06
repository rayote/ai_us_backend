from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.schemas.application import ApplicationCreate


class DuplicateApplicationError(Exception):
    """Raised when a participant phone number already has an application."""


class ApplicationRepository(Protocol):
    async def create(self, application: ApplicationCreate) -> str:
        ...


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


def object_id() -> str:
    return str(ObjectId())

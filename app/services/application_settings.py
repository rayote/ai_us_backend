from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol


class ApplicationSettingsRepository(Protocol):
    async def auto_approval_enabled(self) -> bool:
        ...

    async def set_auto_approval(self, enabled: bool, updated_by: str) -> bool:
        ...


class MongoApplicationSettingsRepository:
    _DOCUMENT_ID = "participant_application"

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def auto_approval_enabled(self) -> bool:
        document = await self._collection.find_one({"_id": self._DOCUMENT_ID})
        return bool(document and document.get("auto_approval", False))

    async def set_auto_approval(self, enabled: bool, updated_by: str) -> bool:
        await self._collection.update_one(
            {"_id": self._DOCUMENT_ID},
            {
                "$set": {
                    "auto_approval": enabled,
                    "updated_at": datetime.now(UTC),
                    "updated_by": updated_by,
                }
            },
            upsert=True,
        )
        return enabled

from __future__ import annotations

from typing import Any

from pymongo import ASCENDING, AsyncMongoClient


class MongoDatabase:
    def __init__(self, mongodb_uri: str, database_name: str) -> None:
        self._client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(mongodb_uri)
        self.database = self._client[database_name]

    async def connect(self) -> None:
        await self._client.admin.command("ping")
        await self.database["applications"].create_index(
            [("phone_normalized", ASCENDING)],
            name="applications_phone_normalized_unique",
            unique=True,
        )

    async def close(self) -> None:
        await self._client.close()

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from pymongo import ASCENDING, MongoClient


class AsyncCursor:
    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def sort(self, key: str, direction: int) -> "AsyncCursor":
        self._cursor = self._cursor.sort(key, direction)
        return self

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self

    async def __anext__(self) -> dict[str, Any]:
        document = await asyncio.to_thread(_next_document, self._cursor)
        if document is None:
            raise StopAsyncIteration
        return document


class AsyncCollection:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def insert_one(self, document: dict[str, Any]) -> Any:
        return await asyncio.to_thread(self._collection.insert_one, document)

    async def create_index(self, keys: list[tuple[str, int]], **kwargs: Any) -> str:
        return await asyncio.to_thread(self._collection.create_index, keys, **kwargs)

    async def find_one(self, filters: dict[str, Any]) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._collection.find_one, filters)

    def find(self, filters: dict[str, Any]) -> AsyncCursor:
        return AsyncCursor(self._collection.find(filters))

    async def update_one(self, filters: dict[str, Any], update: dict[str, Any], **kwargs: Any) -> Any:
        return await asyncio.to_thread(self._collection.update_one, filters, update, **kwargs)

    async def update_many(self, filters: dict[str, Any], update: dict[str, Any]) -> Any:
        return await asyncio.to_thread(self._collection.update_many, filters, update)

    async def find_one_and_update(self, filters: dict[str, Any], update: dict[str, Any], **kwargs: Any) -> Any:
        return await asyncio.to_thread(self._collection.find_one_and_update, filters, update, **kwargs)


def _next_document(cursor: Any) -> dict[str, Any] | None:
    try:
        return next(cursor)
    except StopIteration:
        return None


class AsyncDatabase:
    def __init__(self, database: Any) -> None:
        self._database = database

    def __getitem__(self, name: str) -> AsyncCollection:
        return AsyncCollection(self._database[name])


class MongoDatabase:
    def __init__(self, mongodb_uri: str, database_name: str) -> None:
        self._client = MongoClient(mongodb_uri)
        self.database = AsyncDatabase(self._client[database_name])

    async def connect(self) -> None:
        await asyncio.to_thread(self._client.admin.command, "ping")
        await self.database["applications"].create_index(
            [("phone_normalized", ASCENDING)],
            name="applications_phone_normalized_unique",
            unique=True,
        )
        await self.database["participants"].create_index(
            [("phone_normalized", ASCENDING)],
            name="participants_phone_normalized_unique",
            unique=True,
        )
        await self.database["researchers"].create_index(
            [("username", ASCENDING)],
            name="researchers_username_unique",
            unique=True,
        )
        await self.database["survey_definitions"].create_index(
            [("survey_round", ASCENDING), ("survey_version", ASCENDING)],
            name="survey_definitions_round_version_unique",
            unique=True,
        )
        await self.database["survey_responses"].create_index(
            [("participant_id", ASCENDING), ("survey_round", ASCENDING), ("survey_version", ASCENDING)],
            name="survey_responses_participant_round_version_unique",
            unique=True,
        )
        await self.database["chat_submissions"].create_index(
            [("participant_id", ASCENDING), ("submission_point", ASCENDING), ("submitted_at", ASCENDING)],
            name="chat_submissions_participant_point",
        )
        await self.database["submission_jobs"].create_index(
            [("job_type", ASCENDING), ("idempotency_key", ASCENDING)],
            name="submission_jobs_idempotency_unique",
            unique=True,
        )
        await self.database["submission_jobs"].create_index(
            [("status", ASCENDING), ("created_at", ASCENDING)],
            name="submission_jobs_processing_order",
        )

    async def close(self) -> None:
        self._client.close()

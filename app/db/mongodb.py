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
        await self._client.close()

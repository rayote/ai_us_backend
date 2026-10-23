from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from app.schemas.survey import SurveyProgressRecord, SurveyProgressSave
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


class ProgressConflictError(Exception):
    pass


class SurveyProgressRepository(Protocol):
    async def get(
        self, participant_id: str, survey_round: int, survey_version: str
    ) -> SurveyProgressRecord | None: ...

    async def save(
        self, participant_id: str, progress: SurveyProgressSave
    ) -> SurveyProgressRecord: ...

    async def list_incomplete(
        self, survey_round: int, survey_version: str
    ) -> list[dict[str, Any]]: ...


class MongoSurveyProgressRepository:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def list_incomplete(
        self, survey_round: int, survey_version: str
    ) -> list[dict[str, Any]]:
        cursor = self._collection.find(
            {
                "survey_round": survey_round,
                "survey_version": survey_version,
                "completed": {"$ne": True},
            }
        ).sort("updated_at", -1)
        return [
            {
                **SurveyProgressRecord.model_validate(document).model_dump(
                    by_alias=True, mode="json"
                ),
                "participantId": str(document["participant_id"]),
                "pageHistory": document.get("page_history", []),
            }
            async for document in cursor
        ]

    async def get(
        self, participant_id: str, survey_round: int, survey_version: str
    ) -> SurveyProgressRecord | None:
        document = await self._collection.find_one(
            {
                "participant_id": participant_id,
                "survey_round": survey_round,
                "survey_version": survey_version,
            }
        )
        return SurveyProgressRecord.model_validate(document) if document else None

    async def save(
        self, participant_id: str, progress: SurveyProgressSave
    ) -> SurveyProgressRecord:
        now = datetime.now(UTC)
        snapshot = progress.model_dump(
            exclude={"revision", "client_saved_at", "completed_page_title"}
        )
        snapshot.update(
            {"participant_id": participant_id, "updated_at": now, "completed": False}
        )
        try:
            document = await self._collection.find_one_and_update(
                {
                    "_id": f"{participant_id}:{progress.survey_round}:{progress.survey_version}",
                    "revision": progress.revision,
                    "completed": {"$ne": True},
                },
                {
                    "$set": snapshot,
                    "$inc": {"revision": 1},
                    "$push": {
                        "page_history": {
                            "$each": [
                                {
                                    "page": progress.completed_page,
                                    "title": progress.completed_page_title,
                                    "next_page": progress.next_page,
                                    "saved_at": now,
                                    "client_saved_at": progress.client_saved_at,
                                }
                            ],
                            "$slice": -1000,
                        }
                    },
                },
                upsert=progress.revision == 0,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError as error:
            raise ProgressConflictError from error
        if document is None:
            raise ProgressConflictError
        return SurveyProgressRecord.model_validate(document)

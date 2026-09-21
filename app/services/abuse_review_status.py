from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, Protocol

from app.schemas.abuse_review import AbuseReviewStatus


def abuse_candidate_key(survey_round: int, survey_version: str, phone: str, paired_phone: str | None) -> str:
    phones = sorted((phone, paired_phone or ""))
    raw = "|".join((str(survey_round), survey_version, *phones))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class AbuseReviewStatusRepository(Protocol):
    async def list_statuses(self, survey_round: int, survey_version: str) -> list[AbuseReviewStatus]: ...

    async def set_status(
        self,
        survey_round: int,
        survey_version: str,
        candidate_key: str,
        reviewed: bool,
        reviewer: str,
    ) -> AbuseReviewStatus: ...


class MongoAbuseReviewStatusRepository:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def list_statuses(self, survey_round: int, survey_version: str) -> list[AbuseReviewStatus]:
        cursor = self._collection.find({"survey_round": survey_round, "survey_version": survey_version})
        return [
            AbuseReviewStatus(
                candidateKey=document["candidate_key"],
                reviewed=document["reviewed"],
                reviewedAt=document.get("reviewed_at").isoformat() if document.get("reviewed_at") else None,
                reviewedBy=document.get("reviewed_by"),
            )
            async for document in cursor
        ]

    async def set_status(
        self,
        survey_round: int,
        survey_version: str,
        candidate_key: str,
        reviewed: bool,
        reviewer: str,
    ) -> AbuseReviewStatus:
        reviewed_at = datetime.now(UTC)
        await self._collection.update_one(
            {"survey_round": survey_round, "survey_version": survey_version, "candidate_key": candidate_key},
            {"$set": {"reviewed": reviewed, "reviewed_at": reviewed_at, "reviewed_by": reviewer}},
            upsert=True,
        )
        return AbuseReviewStatus(
            candidateKey=candidate_key,
            reviewed=reviewed,
            reviewedAt=reviewed_at.isoformat(),
            reviewedBy=reviewer,
        )

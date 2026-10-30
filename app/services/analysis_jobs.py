from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from app.schemas.abuse_review import AbuseReviewReport
from app.schemas.jobs import Job
from app.services.jobs import MongoJobRepository, _job_from_document
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

ANALYSIS_HEARTBEAT_ID = "analysis-worker"
ANALYSIS_CACHE_SECONDS = 300


def analysis_job_id(survey_round: int | None, survey_version: str | None) -> str:
    scope = json.dumps([survey_round, survey_version], ensure_ascii=True)
    return hashlib.sha256(scope.encode()).hexdigest()[:24]


def paginate_report(
    report: AbuseReviewReport,
    pair_page: int = 1,
    pair_page_size: int = 200,
    speed_page: int = 1,
    pairing_page: int = 1,
    combined_page: int = 1,
) -> AbuseReviewReport:
    size = max(1, min(pair_page_size, 5000))
    changes: dict[str, Any] = {"pair_page_size": size}
    for field, page, count in (
        (
            "candidates",
            pair_page,
            report.pairing_candidate_count + report.combined_candidate_count,
        ),
        ("speed_candidates", speed_page, report.speed_candidate_count),
        ("pairing_candidates", pairing_page, report.pairing_candidate_count),
        ("combined_candidates", combined_page, report.combined_candidate_count),
    ):
        prefix = {
            "candidates": "pair",
            "speed_candidates": "speed",
            "pairing_candidates": "pairing",
            "combined_candidates": "combined",
        }[field]
        page = max(1, page)
        changes[field] = getattr(report, field)[(page - 1) * size : page * size]
        changes[prefix + "_page"] = page
        changes[prefix + "_page_count"] = max(1, (count + size - 1) // size)
    return report.model_copy(update=changes)


class MongoAnalysisJobRepository(MongoJobRepository):
    async def request_report(
        self,
        survey_round: int | None,
        survey_version: str | None,
        refresh: bool = False,
    ) -> Job:
        job_id = analysis_job_id(survey_round, survey_version)
        now = datetime.now(UTC)
        document = {
            "job_type": "abuse_review",
            "idempotency_key": job_id,
            "payload": {
                "jobId": job_id,
                "surveyRound": survey_round,
                "surveyVersion": survey_version,
            },
            "status": "queued",
            "attempts": 0,
            "created_at": now,
            "processed_at": None,
            "error": None,
            "result": None,
        }
        try:
            saved = await self._collection.find_one_and_update(
                {
                    "_id": ObjectId(job_id),
                    "$or": [
                        {"status": {"$nin": ["queued", "processing", "completed"]}},
                        {
                            "status": "completed",
                            **(
                                {}
                                if refresh
                                else {
                                    "processed_at": {
                                        "$lt": now
                                        - timedelta(seconds=ANALYSIS_CACHE_SECONDS)
                                    }
                                }
                            ),
                        },
                    ],
                },
                {"$set": document},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            saved = await self._collection.find_one(
                {"_id": ObjectId(job_id)}, projection={"result": 0}
            )
        if saved is None:
            raise RuntimeError("Analysis job could not be retrieved")
        return _job_from_document(saved)

    async def get(self, job_id: str) -> Job | None:
        if not ObjectId.is_valid(job_id):
            return None
        document = await self._collection.find_one(
            {"_id": ObjectId(job_id)}, projection={"result": 0}
        )
        return _job_from_document(document) if document else None

    async def store_report(self, job_id: str, report: AbuseReviewReport) -> None:
        encoded = await asyncio.to_thread(
            lambda: gzip.compress(report.model_dump_json(by_alias=True).encode())
        )
        if len(encoded) > 14 * 1024 * 1024:
            raise ValueError(
                "분석 결과가 너무 큽니다. 설문 회차와 버전을 선택해 주세요."
            )
        await self._collection.update_one(
            {"_id": ObjectId(job_id), "status": "processing"},
            {"$set": {"result": encoded}},
        )

    async def report(self, job_id: str) -> AbuseReviewReport | None:
        if not ObjectId.is_valid(job_id):
            return None
        document = await self._collection.find_one(
            {"_id": ObjectId(job_id), "status": "completed"}, projection={"result": 1}
        )
        if not document or not document.get("result"):
            return None
        return await asyncio.to_thread(
            lambda: AbuseReviewReport.model_validate_json(
                gzip.decompress(document["result"])
            )
        )

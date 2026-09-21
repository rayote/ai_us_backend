from __future__ import annotations

from pydantic import BaseModel, Field


class SimilarityBucket(BaseModel):
    label: str
    minimum_percent: int = Field(alias="minimumPercent")
    pair_count: int = Field(alias="pairCount")


class AbuseReviewCandidate(BaseModel):
    phone: str
    paired_phone: str = Field(alias="pairedPhone")
    similarity_percent: int = Field(alias="similarityPercent")
    similarity_bucket: str = Field(alias="similarityBucket")
    reasons: list[str]
    review_priority: str = Field(alias="reviewPriority")
    left: dict[str, object]
    right: dict[str, object]


class AbuseReviewReport(BaseModel):
    survey_round: int | None = Field(default=None, alias="surveyRound")
    survey_version: str | None = Field(default=None, alias="surveyVersion")
    total_responses: int = Field(alias="totalResponses")
    similarity_buckets: list[SimilarityBucket] = Field(alias="similarityBuckets")
    candidates: list[AbuseReviewCandidate]

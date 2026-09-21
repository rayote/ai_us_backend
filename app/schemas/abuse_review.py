from __future__ import annotations

from pydantic import BaseModel, Field


class SimilarityBucket(BaseModel):
    label: str
    minimum_percent: int = Field(alias="minimumPercent")
    pair_count: int = Field(alias="pairCount")


class SpeedBucket(BaseModel):
    label: str
    maximum_seconds: int | None = Field(default=None, alias="maximumSeconds")
    response_count: int = Field(alias="responseCount")


class AbuseReviewCandidate(BaseModel):
    candidate_key: str = Field(alias="candidateKey")
    phone: str
    paired_phone: str | None = Field(default=None, alias="pairedPhone")
    similarity_percent: int | None = Field(default=None, alias="similarityPercent")
    similarity_bucket: str | None = Field(default=None, alias="similarityBucket")
    reasons: list[str]
    review_priority: str = Field(alias="reviewPriority")
    candidate_type: str = Field(alias="candidateType")
    left: dict[str, object] = Field(default_factory=dict)
    right: dict[str, object] = Field(default_factory=dict)


class AbuseReviewStatusUpdate(BaseModel):
    phone: str
    paired_phone: str | None = Field(default=None, alias="pairedPhone")
    reviewed: bool


class AbuseReviewStatus(BaseModel):
    candidate_key: str = Field(alias="candidateKey")
    reviewed: bool
    reviewed_at: str | None = Field(default=None, alias="reviewedAt")
    reviewed_by: str | None = Field(default=None, alias="reviewedBy")


class AbuseReviewReport(BaseModel):
    survey_round: int | None = Field(default=None, alias="surveyRound")
    survey_version: str | None = Field(default=None, alias="surveyVersion")
    total_responses: int = Field(alias="totalResponses")
    similarity_buckets: list[SimilarityBucket] = Field(alias="similarityBuckets")
    speed_buckets: list[SpeedBucket] = Field(alias="speedBuckets")
    candidates: list[AbuseReviewCandidate]
    speed_candidates: list[AbuseReviewCandidate] = Field(alias="speedCandidates")
    pairing_candidates: list[AbuseReviewCandidate] = Field(alias="pairingCandidates")
    combined_candidates: list[AbuseReviewCandidate] = Field(alias="combinedCandidates")
    speed_candidate_count: int = Field(alias="speedCandidateCount")
    pairing_candidate_count: int = Field(alias="pairingCandidateCount")
    combined_candidate_count: int = Field(alias="combinedCandidateCount")

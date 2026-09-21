from __future__ import annotations

import json
from typing import Any

from app.schemas.abuse_review import AbuseReviewCandidate, AbuseReviewReport, SimilarityBucket
from app.schemas.survey import SurveyDefinition, SurveyResponseRecord
from app.services.auth import ParticipantAccountRepository
from app.services.surveys import SurveyResponseRepository


def _detail_value(detail: dict[str, object] | None, *keys: str) -> object | None:
    if not detail:
        return None
    for key in keys:
        if key in detail:
            return detail[key]
    return None


def _number(value: object | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _answer_items(response: SurveyResponseRecord) -> dict[str, str]:
    return {key: _canonical(value) for key, value in response.answers.items() if value is not None}


def _required_complete(response: SurveyResponseRecord, definition: SurveyDefinition | None) -> bool | None:
    explicit = _detail_value(response.detail, "requiredComplete", "required_complete")
    if isinstance(explicit, bool):
        return explicit
    if definition is None:
        return None
    required_keys = []
    for question in definition.questions:
        data = question.model_dump()
        if data.get("required") is not True or data.get("logic"):
            continue
        required_keys.append(question.key)
    if not required_keys:
        return None
    answers = response.answers
    return all(key in answers and answers[key] not in (None, "", []) for key in required_keys)


def _similarity(left: SurveyResponseRecord, right: SurveyResponseRecord) -> int:
    left_answers = _answer_items(left)
    right_answers = _answer_items(right)
    keys = set(left_answers) | set(right_answers)
    if not keys:
        return 0
    matches = sum(left_answers.get(key) == right_answers.get(key) for key in keys)
    return round(matches / len(keys) * 100)


def _bucket(percent: int) -> tuple[str, int]:
    if percent == 100:
        return "100% 일치", 100
    if percent >= 95:
        return "95~99% 일치", 95
    if percent >= 90:
        return "90~94% 일치", 90
    return "90% 미만", 0


def _priority(reasons: list[str], similarity: int) -> str:
    if len(reasons) >= 3 or (similarity >= 95 and len(reasons) >= 2):
        return "우선 검토"
    if reasons:
        return "확인 필요"
    return "참고"


class AbuseReviewService:
    def __init__(
        self,
        participants: ParticipantAccountRepository,
        responses: SurveyResponseRepository,
        definitions: Any,
    ) -> None:
        self._participants = participants
        self._responses = responses
        self._definitions = definitions

    async def report(self, survey_round: int | None = None, survey_version: str | None = None) -> AbuseReviewReport:
        if survey_round is not None and survey_version:
            responses = await self._responses.list_responses(survey_round, survey_version)
            definition = await self._definitions.get(survey_round, survey_version)
        else:
            responses = await self._responses.list_all_responses()
            definition = None
        participants = {participant.participant_id: participant for participant in await self._participants.list_participants()}
        buckets = {"100% 일치": 0, "95~99% 일치": 0, "90~94% 일치": 0, "90% 미만": 0}
        candidates: list[AbuseReviewCandidate] = []
        for index, left in enumerate(responses):
            for right in responses[index + 1:]:
                similarity = _similarity(left, right)
                bucket, minimum = _bucket(similarity)
                buckets[bucket] += 1
                left_profile = participants.get(left.participant_id)
                right_profile = participants.get(right.participant_id)
                if left_profile is None or right_profile is None:
                    continue
                reasons: list[str] = []
                left_detail = left.detail or {}
                right_detail = right.detail or {}
                left_page = _number(_detail_value(left_detail, "visitedPageCount", "visited_page_count", "pageCount", "page_count"))
                right_page = _number(_detail_value(right_detail, "visitedPageCount", "visited_page_count", "pageCount", "page_count"))
                left_total = _number(_detail_value(left_detail, "totalPageCount", "total_page_count"))
                right_total = _number(_detail_value(right_detail, "totalPageCount", "total_page_count"))
                if left_page is not None and left_total and left_page < left_total:
                    reasons.append("방문한 페이지 수가 전체 페이지 수보다 적음")
                if right_page is not None and right_total and right_page < right_total:
                    reasons.append("방문한 페이지 수가 전체 페이지 수보다 적음")
                if _required_complete(left, definition) is True and _required_complete(right, definition) is True:
                    reasons.append("필수 응답은 모두 존재함")
                active_values = [_number(_detail_value(left_detail, "activeSeconds", "active_seconds")), _number(_detail_value(right_detail, "activeSeconds", "active_seconds"))]
                if all(value is not None and value < 60 for value in active_values):
                    reasons.append("활동시간이 비정상적으로 짧음")
                if similarity >= 90:
                    reasons.append("다른 참여자와 응답 패턴이 매우 유사함")
                if not reasons:
                    continue
                candidates.append(
                    AbuseReviewCandidate(
                        phone=left_profile.phone,
                        pairedPhone=right_profile.phone,
                        similarityPercent=similarity,
                        similarityBucket=bucket,
                        reasons=sorted(set(reasons)),
                        reviewPriority=_priority(reasons, similarity),
                        left={"submittedAt": left.submitted_at.isoformat(), "activeSeconds": left_detail.get("activeSeconds", left_detail.get("active_seconds")), "pageCount": left_detail.get("visitedPageCount", left_detail.get("pageCount"))},
                        right={"submittedAt": right.submitted_at.isoformat(), "activeSeconds": right_detail.get("activeSeconds", right_detail.get("active_seconds")), "pageCount": right_detail.get("visitedPageCount", right_detail.get("pageCount"))},
                    )
                )
        candidates.sort(key=lambda item: (-len(item.reasons), -item.similarity_percent, item.phone, item.paired_phone))
        return AbuseReviewReport(
            surveyRound=survey_round,
            surveyVersion=survey_version,
            totalResponses=len(responses),
            similarityBuckets=[
                SimilarityBucket(label=label, minimumPercent=minimum, pairCount=buckets[label])
                for label, minimum in (("100% 일치", 100), ("95~99% 일치", 95), ("90~94% 일치", 90), ("90% 미만", 0))
            ],
            candidates=candidates[:200],
        )

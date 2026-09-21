from __future__ import annotations

import json
from typing import Any

from app.schemas.abuse_review import AbuseReviewCandidate, AbuseReviewReport, SimilarityBucket, SpeedBucket
from app.schemas.survey import SurveyDefinition, SurveyResponseRecord
from app.services.auth import ParticipantAccountRepository
from app.services.surveys import SurveyResponseRepository
from app.services.abuse_review_status import abuse_candidate_key


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


def _speed_reasons(response: SurveyResponseRecord, survey_round: int | None) -> list[str]:
    detail = response.detail or {}
    wall_clock_seconds = _number(_detail_value(detail, "wallClockSeconds", "wall_clock_seconds"))
    reasons: list[str] = []
    threshold = 300 if survey_round == 2 else 600
    if wall_clock_seconds is not None and wall_clock_seconds <= threshold:
        reasons.append(f"응답시간이 {threshold // 60}분 이하로 짧음")
    return reasons


def _speed_bucket(response: SurveyResponseRecord) -> str:
    seconds = _number(_detail_value(response.detail, "wallClockSeconds", "wall_clock_seconds"))
    if seconds is None:
        return "응답시간 확인 불가"
    if seconds <= 300:
        return "5분 이하"
    if seconds <= 600:
        return "5분 초과~10분 이하"
    return "10분 초과"


def _candidate_detail(response: SurveyResponseRecord) -> dict[str, object]:
    detail = response.detail or {}
    return {
        "submittedAt": response.submitted_at.isoformat(),
        "activeSeconds": detail.get("activeSeconds", detail.get("active_seconds")),
        "wallClockSeconds": detail.get("wallClockSeconds", detail.get("wall_clock_seconds")),
        "pageCount": detail.get("visitedPageCount", detail.get("pageCount")),
        "totalPageCount": detail.get("totalPageCount", detail.get("total_page_count")),
        "navigationCount": detail.get("navigationCount", detail.get("navigation_count")),
        "resumeCount": detail.get("resumeCount", detail.get("resume_count")),
    }


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
        participants = {
            participant.participant_id: participant for participant in await self._participants.list_participants()
        }
        buckets = {"100% 일치": 0, "95~99% 일치": 0, "90~94% 일치": 0, "90% 미만": 0}
        speed_buckets = {"5분 이하": 0, "5분 초과~10분 이하": 0, "10분 초과": 0, "응답시간 확인 불가": 0}
        candidates: list[AbuseReviewCandidate] = []
        speed_candidates: list[AbuseReviewCandidate] = []
        pairing_candidates: list[AbuseReviewCandidate] = []
        combined_candidates: list[AbuseReviewCandidate] = []
        for response in responses:
            profile = participants.get(response.participant_id)
            speed_bucket = _speed_bucket(response)
            speed_buckets[speed_bucket] += 1
            response_round = survey_round if survey_round is not None else response.survey_round
            reasons = _speed_reasons(response, response_round)
            if profile is not None and reasons:
                speed_candidates.append(
                    AbuseReviewCandidate(
                        candidateKey=abuse_candidate_key(response.survey_round, response.survey_version, profile.phone, None),
                        phone=profile.phone,
                        reasons=reasons,
                        reviewPriority="확인 필요",
                        candidateType="개인 단위 속도 이상",
                        left=_candidate_detail(response),
                    )
                )
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
                left_page = _number(
                    _detail_value(left_detail, "visitedPageCount", "visited_page_count", "pageCount", "page_count")
                )
                right_page = _number(
                    _detail_value(right_detail, "visitedPageCount", "visited_page_count", "pageCount", "page_count")
                )
                left_total = _number(_detail_value(left_detail, "totalPageCount", "total_page_count"))
                right_total = _number(_detail_value(right_detail, "totalPageCount", "total_page_count"))
                if left_page is not None and left_total and left_page < left_total:
                    reasons.append("방문한 페이지 수가 전체 페이지 수보다 적음")
                if right_page is not None and right_total and right_page < right_total:
                    reasons.append("방문한 페이지 수가 전체 페이지 수보다 적음")
                if _required_complete(left, definition) is True and _required_complete(right, definition) is True:
                    reasons.append("필수 응답은 모두 존재함")
                response_threshold = 300 if (survey_round or left.survey_round) == 2 else 600
                response_times = [
                    _number(_detail_value(left_detail, "wallClockSeconds", "wall_clock_seconds")),
                    _number(_detail_value(right_detail, "wallClockSeconds", "wall_clock_seconds")),
                ]
                if all(value is not None and value <= response_threshold for value in response_times):
                    reasons.append(f"응답시간이 {response_threshold // 60}분 이하로 짧음")
                if similarity >= 90:
                    reasons.append("다른 참여자와 응답 패턴이 매우 유사함")
                if not reasons:
                    continue
                candidate = AbuseReviewCandidate(
                    candidateKey=abuse_candidate_key(left.survey_round, left.survey_version, left_profile.phone, right_profile.phone),
                    phone=left_profile.phone,
                    pairedPhone=right_profile.phone,
                    similarityPercent=similarity,
                    similarityBucket=bucket,
                    reasons=sorted(set(reasons)),
                    reviewPriority=_priority(reasons, similarity),
                    candidateType="복합 의심" if len(set(reasons)) >= 2 else "응답 패턴 유사 묶음",
                    left=_candidate_detail(left),
                    right=_candidate_detail(right),
                )
                candidates.append(candidate)
                if candidate.candidate_type == "복합 의심":
                    combined_candidates.append(candidate)
                else:
                    pairing_candidates.append(candidate)
        candidates.sort(key=lambda item: (-len(item.reasons), -item.similarity_percent, item.phone, item.paired_phone))
        return AbuseReviewReport(
            surveyRound=survey_round,
            surveyVersion=survey_version,
            totalResponses=len(responses),
            similarityBuckets=[
                SimilarityBucket(label=label, minimumPercent=minimum, pairCount=buckets[label])
                for label, minimum in (("100% 일치", 100), ("95~99% 일치", 95), ("90~94% 일치", 90), ("90% 미만", 0))
            ],
            speedBuckets=[
                SpeedBucket(label=label, maximumSeconds=maximum, responseCount=speed_buckets[label])
                for label, maximum in (
                    ("5분 이하", 300),
                    ("5분 초과~10분 이하", 600),
                    ("10분 초과", None),
                    ("응답시간 확인 불가", None),
                )
            ],
            candidates=candidates[:200],
            speedCandidates=speed_candidates[:200],
            pairingCandidates=pairing_candidates[:200],
            combinedCandidates=combined_candidates[:200],
        )

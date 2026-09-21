import asyncio
from datetime import UTC, datetime

from app.schemas.survey import SurveyDefinition, SurveyQuestion, SurveyResponseRecord
from app.services.abuse_review import AbuseReviewService
from app.services.auth import ParticipantAccount


class Responses:
    def __init__(self, records: list[SurveyResponseRecord]) -> None:
        self.records = records

    async def list_responses(self, survey_round: int, survey_version: str) -> list[SurveyResponseRecord]:
        return self.records

    async def list_all_responses(self) -> list[SurveyResponseRecord]:
        return self.records


class Participants:
    async def list_participants(self) -> list[ParticipantAccount]:
        return [
            ParticipantAccount("internal-a", "010-1111-2222", "hash", False),
            ParticipantAccount("internal-b", "010-3333-4444", "hash", False),
        ]


class Definitions:
    async def get(self, survey_round: int, survey_version: str) -> SurveyDefinition:
        return SurveyDefinition(
            surveyRound=survey_round,
            surveyVersion=survey_version,
            questions=[SurveyQuestion(key="q1", csvColumn="q1", order=1, required=True)],
            createdAt=datetime.now(UTC),
        )


def test_abuse_review_pairs_by_phone_and_groups_similarity() -> None:
    records = [
        SurveyResponseRecord(
            participantId="internal-a",
            surveyRound=1,
            surveyVersion="v2",
            answers={"q1": "same", "q2": "same"},
            submittedAt=datetime.now(UTC),
            detail={"activeSeconds": 30, "wallClockSeconds": 30, "visitedPageCount": 2, "totalPageCount": 4},
        ),
        SurveyResponseRecord(
            participantId="internal-b",
            surveyRound=1,
            surveyVersion="v2",
            answers={"q1": "same", "q2": "same"},
            submittedAt=datetime.now(UTC),
            detail={"activeSeconds": 35, "wallClockSeconds": 35, "visitedPageCount": 2, "totalPageCount": 4},
        ),
    ]
    report = asyncio.run(AbuseReviewService(Participants(), Responses(records), Definitions()).report(1, "v2"))

    assert report.total_responses == 2
    assert report.candidates[0].phone == "010-1111-2222"
    assert report.candidates[0].paired_phone == "010-3333-4444"
    assert report.candidates[0].similarity_percent == 100
    assert report.candidates[0].similarity_bucket == "100% 일치"
    assert report.speed_buckets[0].label == "5분 이하"
    assert report.speed_buckets[0].response_count == 2
    assert len(report.speed_candidates) == 2
    assert report.combined_candidates[0].candidate_type == "복합 의심"
    assert report.pairing_candidates == []
    assert len(report.groups) == 1
    assert report.groups[0].member_count == 2
    assert report.groups[0].pair_count == 1
    assert "필수 응답은 모두 존재함" in report.candidates[0].reasons
    assert "방문한 페이지 수가 전체 페이지 수보다 적음" in report.candidates[0].reasons
    assert "응답시간이 10분 이하로 짧음" in report.candidates[0].reasons
    assert "internal-a" not in report.model_dump_json()
    assert "internal-b" not in report.model_dump_json()


def test_second_round_uses_five_minute_speed_threshold() -> None:
    response = SurveyResponseRecord(
        participantId="internal-a",
        surveyRound=2,
        surveyVersion="v2",
        answers={"q1": "same"},
        submittedAt=datetime.now(UTC),
        detail={"wallClockSeconds": 300},
    )
    report = asyncio.run(AbuseReviewService(Participants(), Responses([response]), Definitions()).report(2, "v2"))

    assert report.speed_buckets[0].response_count == 1
    assert report.speed_candidates[0].reasons == ["응답시간이 5분 이하로 짧음"]


def test_low_similarity_pairs_are_not_candidates() -> None:
    records = [
        SurveyResponseRecord(
            participantId="internal-a",
            surveyRound=1,
            surveyVersion="v2",
            answers={"q1": "one", "q2": "two"},
            submittedAt=datetime.now(UTC),
            detail={"wallClockSeconds": 700, "visitedPageCount": 2, "totalPageCount": 4},
        ),
        SurveyResponseRecord(
            participantId="internal-b",
            surveyRound=1,
            surveyVersion="v2",
            answers={"q1": "different", "q2": "other"},
            submittedAt=datetime.now(UTC),
            detail={"wallClockSeconds": 700, "visitedPageCount": 2, "totalPageCount": 4},
        ),
    ]
    report = asyncio.run(AbuseReviewService(Participants(), Responses(records), Definitions()).report(1, "v2"))

    assert [bucket.label for bucket in report.similarity_buckets] == ["100% 일치", "95~99% 일치", "90~94% 일치"]
    assert sum(bucket.pair_count for bucket in report.similarity_buckets) == 0
    assert report.candidates == []
    assert report.pairing_candidates == []
    assert report.combined_candidates == []


def test_duplicate_response_documents_count_one_pair() -> None:
    base = SurveyResponseRecord(
        participantId="internal-a",
        surveyRound=1,
        surveyVersion="v2",
        answers={"q1": "same", "q2": "same"},
        submittedAt=datetime.now(UTC),
        detail={"wallClockSeconds": 700},
    )
    paired = base.model_copy(update={"participant_id": "internal-b"})
    report = asyncio.run(
        AbuseReviewService(Participants(), Responses([base, paired, base, paired]), Definitions()).report(1, "v2")
    )

    assert report.similarity_buckets[0].label == "100% 일치"
    assert report.similarity_buckets[0].pair_count == 1
    assert len(report.combined_candidates) == 1

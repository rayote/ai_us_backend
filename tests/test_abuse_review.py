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
            detail={"activeSeconds": 30, "visitedPageCount": 2, "totalPageCount": 4},
        ),
        SurveyResponseRecord(
            participantId="internal-b",
            surveyRound=1,
            surveyVersion="v2",
            answers={"q1": "same", "q2": "same"},
            submittedAt=datetime.now(UTC),
            detail={"activeSeconds": 35, "visitedPageCount": 2, "totalPageCount": 4},
        ),
    ]
    report = asyncio.run(AbuseReviewService(Participants(), Responses(records), Definitions()).report(1, "v2"))

    assert report.total_responses == 2
    assert report.candidates[0].phone == "010-1111-2222"
    assert report.candidates[0].paired_phone == "010-3333-4444"
    assert report.candidates[0].similarity_percent == 100
    assert report.candidates[0].similarity_bucket == "100% 일치"
    assert len(report.speed_candidates) == 2
    assert report.combined_candidates[0].candidate_type == "복합 의심"
    assert report.pairing_candidates == []
    assert "필수 응답은 모두 존재함" in report.candidates[0].reasons
    assert "방문한 페이지 수가 전체 페이지 수보다 적음" in report.candidates[0].reasons
    assert "활동시간이 비정상적으로 짧음" in report.candidates[0].reasons
    assert "internal-a" not in report.model_dump_json()
    assert "internal-b" not in report.model_dump_json()

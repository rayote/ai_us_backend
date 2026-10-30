import asyncio
from datetime import UTC, datetime

from app.schemas.survey import SurveyDefinition, SurveyQuestion, SurveyResponseRecord
from app.services.abuse_review import AbuseReviewService
from app.services.auth import ParticipantAccount


class AnalysisCollection:
    def __init__(self):
        self.documents = {}

    async def find_one(self, query, **kwargs):
        document = self.documents.get(query["_id"])
        if document is None or any(
            document.get(key) != value for key, value in query.items() if key != "_id"
        ):
            return None
        return document.copy()

    async def find_one_and_update(self, query, update, **kwargs):
        from pymongo.errors import DuplicateKeyError

        document = self.documents.get(query["_id"])
        if document and query.get("$or"):
            threshold = query["$or"][1].get("processed_at")
            stale = document["status"] == "completed" and (
                threshold is None or document["processed_at"] < threshold["$lt"]
            )
            if document["status"] in {"queued", "processing"} or (
                document["status"] == "completed" and not stale
            ):
                raise DuplicateKeyError("duplicate scope")
        saved = {"_id": query["_id"], **update["$set"]}
        self.documents[query["_id"]] = saved
        return saved.copy()

    async def update_one(self, query, update, **kwargs):
        document = await self.find_one(query)
        if document:
            self.documents[query["_id"]].update(update["$set"])


def test_analysis_queue_deduplicates_active_jobs_caches_and_retries_failed_jobs() -> (
    None
):
    from datetime import timedelta

    from app.services.analysis_jobs import MongoAnalysisJobRepository
    from bson import ObjectId

    async def check():
        collection = AnalysisCollection()
        repository = MongoAnalysisJobRepository(collection)
        initial, duplicate = await asyncio.gather(
            repository.request_report(1, "v2"), repository.request_report(1, "v2")
        )
        assert initial.id == duplicate.id
        assert len(collection.documents) == 1
        document = collection.documents[ObjectId(initial.id)]
        document["status"] = "processing"
        assert (await repository.request_report(1, "v2")).status == "processing"
        report = await AbuseReviewService(
            Participants(), Responses([]), Definitions()
        ).report(1, "v2", 1, 100000)
        await repository.store_report(initial.id, report)
        await repository.complete(initial.id)
        assert (await repository.request_report(1, "v2")).status == "completed"
        assert (await repository.report(initial.id)).model_dump() == report.model_dump()
        document["processed_at"] = datetime.now(UTC) - timedelta(minutes=6)
        assert (await repository.request_report(1, "v2")).status == "queued"
        assert await repository.report(initial.id) is None
        collection.documents[ObjectId(initial.id)]["status"] = "failed"
        assert (await repository.request_report(1, "v2")).status == "queued"
        assert (await repository.request_report(2, "v2")).id != initial.id
        collection.documents[ObjectId(initial.id)].update(
            status="completed", processed_at=datetime.now(UTC)
        )
        assert (
            await repository.request_report(1, "v2", refresh=True)
        ).status == "queued"
        assert await repository.get("invalid") is None

    asyncio.run(check())


def test_analysis_api_queues_without_computing_and_returns_saved_result(
    monkeypatch,
) -> None:
    from app.core.security import create_access_token
    from app.core.settings import Settings
    from app.main import create_app
    from app.services.analysis_jobs import MongoAnalysisJobRepository
    from bson import ObjectId
    from fastapi.testclient import TestClient

    collection = AnalysisCollection()
    repository = MongoAnalysisJobRepository(collection)
    settings = Settings(
        "test",
        None,
        "ai_us_test",
        (),
        jwt_secret="test-secret-that-is-at-least-32-characters-long",
    )
    token = create_access_token("researcher", "researcher", settings.jwt_secret, 60)
    headers = {"Authorization": f"Bearer {token}"}
    participant_headers = {
        "Authorization": "Bearer "
        + create_access_token("participant", "participant", settings.jwt_secret, 60)
    }
    report = asyncio.run(
        AbuseReviewService(Participants(), Responses([]), Definitions()).report(
            1, "v2", 1, 100000
        )
    )

    async def forbidden(*args, **kwargs):
        raise AssertionError("API must never run the pair comparison")

    monkeypatch.setattr(AbuseReviewService, "report", forbidden)
    path = "/api/v1/researcher/abuse-review/jobs"
    with TestClient(create_app(settings, analysis_job_repository=repository)) as client:
        assert (
            client.post(path + "?survey_round=1&survey_version=v2").status_code == 401
        )
        assert (
            client.post(
                path + "?survey_round=1&survey_version=v2", headers=participant_headers
            ).status_code
            == 403
        )
        created = client.post(
            path + "?survey_round=1&survey_version=v2", headers=headers
        )
        assert created.status_code == 202
        job_id = created.json()["jobId"]
        assert (
            client.post(
                path + "?survey_round=1&survey_version=v2", headers=headers
            ).json()["jobId"]
            == job_id
        )
        pending = client.get(path + "/" + job_id, headers=headers)
        assert pending.json()["status"] == "queued"
        assert pending.json()["result"] is None
        assert client.get("/health").status_code == 200
        assert client.get(path + "/invalid", headers=headers).status_code == 404
        assert (
            client.put(
                "/api/v1/researcher/abuse-review/statuses?survey_round=1&survey_version=v2",
                headers=headers,
                json={"phone": "test", "reviewed": True},
            ).status_code
            == 409
        )
        collection.documents[ObjectId(job_id)]["status"] = "processing"
        asyncio.run(repository.store_report(job_id, report))
        asyncio.run(repository.complete(job_id))
        completed = client.get(path + "/" + job_id, headers=headers)
        assert completed.json()["status"] == "completed"
        assert completed.json()["result"]["totalResponses"] == 0
        assert (
            client.get(
                "/api/v1/researcher/abuse-review?survey_round=1&survey_version=v2",
                headers=headers,
            ).json()["totalResponses"]
            == 0
        )
        assert (
            client.post(
                path + "?survey_round=1&survey_version=v2&refresh=true", headers=headers
            ).json()["status"]
            == "queued"
        )


class Responses:
    def __init__(self, records: list[SurveyResponseRecord]) -> None:
        self.records = records

    async def list_responses(
        self, survey_round: int, survey_version: str
    ) -> list[SurveyResponseRecord]:
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
            questions=[
                SurveyQuestion(key="q1", csvColumn="q1", order=1, required=True)
            ],
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
            detail={
                "activeSeconds": 30,
                "wallClockSeconds": 30,
                "visitedPageCount": 2,
                "totalPageCount": 4,
            },
        ),
        SurveyResponseRecord(
            participantId="internal-b",
            surveyRound=1,
            surveyVersion="v2",
            answers={"q1": "same", "q2": "same"},
            submittedAt=datetime.now(UTC),
            detail={
                "activeSeconds": 35,
                "wallClockSeconds": 35,
                "visitedPageCount": 2,
                "totalPageCount": 4,
            },
        ),
    ]
    report = asyncio.run(
        AbuseReviewService(Participants(), Responses(records), Definitions()).report(
            1, "v2"
        )
    )

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


def test_full_worker_snapshot_retains_candidates_for_cached_pagination() -> None:
    from app.services.analysis_jobs import paginate_report

    base = SurveyResponseRecord(
        participantId="internal-a",
        surveyRound=1,
        surveyVersion="v2",
        answers={"q1": "same"},
        submittedAt=datetime.now(UTC),
        detail={"wallClockSeconds": 30},
    )
    records = [base, base.model_copy(update={"participant_id": "internal-b"})]
    report = asyncio.run(
        AbuseReviewService(Participants(), Responses(records), Definitions()).report(
            1, "v2", pair_page_size=1, include_all_candidates=True
        )
    )
    assert len(report.speed_candidates) == 2
    page = paginate_report(report, pair_page_size=1, speed_page=2)
    assert page.speed_candidates[0].phone == "010-3333-4444"
    assert page.speed_page_count == 2
    assert len(report.speed_candidates) == 2


def test_second_round_uses_five_minute_speed_threshold() -> None:
    response = SurveyResponseRecord(
        participantId="internal-a",
        surveyRound=2,
        surveyVersion="v2",
        answers={"q1": "same"},
        submittedAt=datetime.now(UTC),
        detail={"wallClockSeconds": 300},
    )
    report = asyncio.run(
        AbuseReviewService(Participants(), Responses([response]), Definitions()).report(
            2, "v2"
        )
    )

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
            detail={
                "wallClockSeconds": 700,
                "visitedPageCount": 2,
                "totalPageCount": 4,
            },
        ),
        SurveyResponseRecord(
            participantId="internal-b",
            surveyRound=1,
            surveyVersion="v2",
            answers={"q1": "different", "q2": "other"},
            submittedAt=datetime.now(UTC),
            detail={
                "wallClockSeconds": 700,
                "visitedPageCount": 2,
                "totalPageCount": 4,
            },
        ),
    ]
    report = asyncio.run(
        AbuseReviewService(Participants(), Responses(records), Definitions()).report(
            1, "v2"
        )
    )

    assert [bucket.label for bucket in report.similarity_buckets] == [
        "100% 일치",
        "95~99% 일치",
        "90~94% 일치",
    ]
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
        AbuseReviewService(
            Participants(), Responses([base, paired, base, paired]), Definitions()
        ).report(1, "v2")
    )

    assert report.similarity_buckets[0].label == "100% 일치"
    assert report.similarity_buckets[0].pair_count == 1
    assert len(report.combined_candidates) == 1

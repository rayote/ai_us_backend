import csv
import io
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from app.schemas.survey import (
    SurveyDefinition,
    SurveyDefinitionCreate,
    SurveyQuestion,
    SurveyResponseRecord,
)
from app.services.surveys import (
    MongoSurveyDefinitionRepository,
    survey_responses_to_csv,
)


def test_t2_reward_addition_preserves_followup_and_appends_csv_column() -> None:
    from scripts.register_frontend_surveys import extract_survey_sets

    frontend = Path(__file__).resolve().parents[2] / "ai_us" / "index.html"
    parts = extract_survey_sets(frontend, 2)["common"]
    spec = next(part for part in parts if part["part"] == 2)
    reward = next(scale for scale in spec["scales"] if scale["scaleId"] == "reward")
    followup = next(scale for scale in spec["scales"] if scale["scaleId"] == "followup")
    assert spec["scales"].index(reward) + 1 == spec["scales"].index(followup)
    question = reward["questions"][0]
    assert question["key"] == "reward.gifticon"
    assert question["required"] is True and question["type"] == "single"
    assert [option["value"] for option in question["options"]] == [1, 2, 3, 4, 5]
    assert len({option["label"] for option in question["options"]}) == 5
    assert [item["key"] for item in followup["questions"]] == ["followup.q1"]
    assert followup["questions"][0]["other"]["when"] == 3
    assert followup["questions"][0]["other"]["field"]["key"] == "followup.q1_reason"
    baseline_spec = deepcopy(spec)
    baseline_spec["scales"] = [
        scale for scale in baseline_spec["scales"] if scale["scaleId"] != "reward"
    ]
    baseline = SurveyDefinitionCreate.model_validate(baseline_spec)
    updated = SurveyDefinitionCreate.model_validate(spec)
    assert updated.survey_version == baseline.survey_version == "t2-part2-v9-1022"
    assert updated.questions[:-1] == baseline.questions
    assert updated.questions[-1].key == "reward.gifticon"
    assert len({item.key for item in updated.questions}) == len(updated.questions)
    now = datetime.now(UTC)

    def definition(source):
        return SurveyDefinition(
            surveyRound=source.survey_round,
            surveyVersion=source.survey_version,
            audience=source.audience,
            questions=source.questions,
            spec=source.raw_spec,
            createdAt=now,
        )

    old_answers = {item.key: "기존 응답 " + item.key for item in baseline.questions}
    old_answers["followup.q1"] = 3
    old_answers["followup.q1_reason"] = "일정이 맞지 않아요."
    responses = [
        SurveyResponseRecord(
            participantId="old-participant",
            surveyRound=2,
            surveyVersion=updated.survey_version,
            answers=deepcopy(old_answers),
            submittedAt=now,
        )
    ]
    for choice in range(1, 6):
        responses.append(
            responses[0].model_copy(
                update={
                    "participant_id": "new-participant-" + str(choice),
                    "answers": {**old_answers, "reward.gifticon": choice},
                }
            )
        )
    old_rows = list(
        csv.reader(
            io.StringIO(survey_responses_to_csv(definition(baseline), responses[:1]))
        )
    )
    new_rows = list(
        csv.reader(io.StringIO(survey_responses_to_csv(definition(updated), responses)))
    )
    assert new_rows[0][:-1] == old_rows[0]
    assert new_rows[1][:-1] == old_rows[1] and new_rows[1][-1] == ""
    assert [row[-1] for row in new_rows[2:]] == ["1", "2", "3", "4", "5"]
    assert all(len(row) == len(new_rows[0]) for row in new_rows)
    assert responses[0].answers == old_answers


class InMemoryDefinitionCollection:
    def __init__(self) -> None:
        self.document: dict[str, object] | None = None

    async def insert_one(self, document: dict[str, object]) -> None:
        self.document = document

    def find(self, filters: dict[str, object]):
        return InMemoryCursor([self.document] if self.document else [])


class InMemoryCursor:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents

    def sort(self, key: str, direction: int) -> "InMemoryCursor":
        return self

    def __aiter__(self):
        self.index = 0
        return self

    async def __anext__(self):
        if self.index >= len(self.documents):
            raise StopAsyncIteration
        document = self.documents[self.index]
        self.index += 1
        return document


def test_csv_export_uses_question_definition_order_and_preserves_missing_answers() -> (
    None
):
    definition = SurveyDefinition(
        surveyRound=2,
        surveyVersion="2026-round-2-v2",
        questions=[
            SurveyQuestion(key="q2", csvColumn="두 번째 문항", order=2),
            SurveyQuestion(key="q1", csvColumn="첫 번째 문항", order=1),
        ],
        createdAt=datetime.now(UTC),
    )
    responses = [
        SurveyResponseRecord(
            participantId="participant-1",
            surveyRound=2,
            surveyVersion="2026-round-2-v2",
            answers={"q1": "응답", "q2": ["선택 A", "선택 B"]},
            submittedAt=datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
        ),
        SurveyResponseRecord(
            participantId="participant-2",
            surveyRound=2,
            surveyVersion="2026-round-2-v2",
            answers={"q1": "다른 응답"},
            submittedAt=datetime(2026, 9, 6, 12, 1, tzinfo=UTC),
        ),
    ]

    csv_text = survey_responses_to_csv(
        definition,
        responses,
        {
            "participant-1": ("01012345678", "초등", 4),
            "participant-2": ("01022223333", "중등", 2),
        },
    )

    assert (
        csv_text.splitlines()[0]
        == "아이디(휴대폰),학교급,학년,surveyRound,surveyVersion,응답 일시(KST),설문 시작 일시(KST),전체 경과시간(초),활동시간(초),재개 횟수,마지막 방문 페이지,방문한 페이지 수,전체 페이지 수,페이지 이동 횟수,첫 번째 문항,두 번째 문항"
    )
    assert csv_text.splitlines()[1].startswith('"=""01012345678""",초등,4,')
    assert csv_text.splitlines()[2].startswith('"=""01022223333""",중등,2,')
    assert csv_text.splitlines()[1].endswith("응답,선택 A; 선택 B")
    assert csv_text.splitlines()[2].endswith("다른 응답,")


def test_csv_export_formats_submission_time_in_kst() -> None:
    definition = SurveyDefinition(
        surveyRound=1,
        surveyVersion="v1",
        questions=[SurveyQuestion(key="q1", csvColumn="첫 번째 문항", order=1)],
        createdAt=datetime.now(UTC),
    )
    response = SurveyResponseRecord(
        participantId="participant-1",
        surveyRound=1,
        surveyVersion="v1",
        answers={"q1": "응답"},
        submittedAt=datetime(2026, 9, 18, 15, 30, tzinfo=UTC),
    )

    csv_text = survey_responses_to_csv(definition, [response])

    assert "응답 일시(KST)" in csv_text.splitlines()[0]
    assert "2026-09-19 00:30:00" in csv_text.splitlines()[1]


def test_csv_export_includes_session_detail_columns_near_submission_time() -> None:
    definition = SurveyDefinition(
        surveyRound=1,
        surveyVersion="v1",
        questions=[SurveyQuestion(key="q1", csvColumn="첫 번째 문항", order=1)],
        createdAt=datetime.now(UTC),
    )
    response = SurveyResponseRecord(
        participantId="participant-1",
        surveyRound=1,
        surveyVersion="v1",
        answers={"q1": "응답"},
        submittedAt=datetime(2026, 9, 18, 15, 30, tzinfo=UTC),
        detail={
            "startedAt": "2026-09-19T00:00:00+09:00",
            "wallClockSeconds": 2430,
            "activeSeconds": 1680,
            "resumeCount": 3,
            "pageCount": 15,
            "visitedPageCount": 12,
            "totalPageCount": 15,
            "navigationCount": 16,
        },
    )

    rows = list(
        csv.reader(io.StringIO(survey_responses_to_csv(definition, [response])))
    )
    start = rows[0].index("응답 일시(KST)")

    assert rows[0][start : start + 9] == [
        "응답 일시(KST)",
        "설문 시작 일시(KST)",
        "전체 경과시간(초)",
        "활동시간(초)",
        "재개 횟수",
        "마지막 방문 페이지",
        "방문한 페이지 수",
        "전체 페이지 수",
        "페이지 이동 횟수",
    ]
    assert rows[1][start : start + 9] == [
        "2026-09-19 00:30:00",
        "2026-09-19 00:00:00",
        "2430",
        "1680",
        "3",
        "15",
        "12",
        "15",
        "16",
    ]


def test_csv_export_reads_nested_composite_answers() -> None:
    definition = SurveyDefinition(
        surveyRound=1,
        surveyVersion="v2",
        questions=[
            SurveyQuestion(key="aiexp.q12_other", csvColumn="기타 순위", order=1),
            SurveyQuestion(key="aiexp.q12_other_txt", csvColumn="기타 대상", order=2),
        ],
        createdAt=datetime.now(UTC),
    )
    response = SurveyResponseRecord(
        participantId="participant-1",
        surveyRound=1,
        surveyVersion="v2",
        answers={
            "aiexp.q12": {
                "aiexp.q12_other": "2",
                "aiexp.q12_other_txt": "상담 선생님",
            }
        },
        submittedAt=datetime.now(UTC),
    )

    csv_text = survey_responses_to_csv(definition, [response])

    assert csv_text.splitlines()[1].endswith("2,상담 선생님")


def test_mongo_definition_storage_uses_csv_column_alias() -> None:
    collection = InMemoryDefinitionCollection()
    repository = MongoSurveyDefinitionRepository(collection)
    definition = SurveyDefinitionCreate(
        surveyRound=1,
        surveyVersion="demo-v1",
        questions=[SurveyQuestion(key="q1", csvColumn="첫 번째 문항", order=1)],
    )

    import asyncio

    stored = asyncio.run(repository.create(definition))

    assert collection.document["questions"] == [
        {"key": "q1", "csvColumn": "첫 번째 문항", "order": 1}
    ]
    assert stored.questions[0].csv_column == "첫 번째 문항"


def test_mongo_definition_storage_accepts_nested_scale_spec() -> None:
    collection = InMemoryDefinitionCollection()
    repository = MongoSurveyDefinitionRepository(collection)
    definition = SurveyDefinitionCreate(
        surveyRound=1,
        audience="elementary",
        surveyVersion="t1-elem-v1-draft",
        scales=[
            {
                "scaleId": "demo",
                "order": 1,
                "scaleName": "인구통계학적 정보 및 일반적 사항",
                "questions": [
                    {
                        "key": "demo.screening1",
                        "no": "screening1",
                        "text": "생성형 AI를 사용한 적이 있나요?",
                        "type": "single",
                        "required": True,
                        "options": [{"value": 1, "label": "없음"}],
                    },
                    {
                        "key": "demo.age",
                        "no": "3",
                        "text": "나이",
                        "type": "number",
                        "required": True,
                    },
                    {
                        "key": "usage.q11",
                        "no": "11",
                        "text": "최근 한 달 생성형 AI 서비스별 사용 빈도",
                        "type": "grid",
                        "required": True,
                        "rows": [
                            {"key": "usage.q11_1", "text": "ChatGPT"},
                            {"key": "usage.q11_2", "text": "Gemini"},
                        ],
                    },
                    {
                        "key": "usage.q16",
                        "no": "16",
                        "text": "AI 서비스 유료 결제 경험이 있나요?",
                        "type": "single",
                        "required": True,
                        "detail": {
                            "when": 2,
                            "field": {
                                "key": "usage.q16_amount",
                                "label": "월 결제 금액",
                            },
                        },
                    },
                    {
                        "key": "usage.q19",
                        "no": "19",
                        "text": "주로 사용하는 AI 서비스",
                        "type": "single",
                        "required": True,
                        "other": {
                            "when": 99,
                            "field": {
                                "key": "usage.q19_other",
                                "label": "기타 서비스명",
                            },
                        },
                    },
                    {
                        "key": "aiexp.q12",
                        "no": "12",
                        "text": "이야기 대상 순위",
                        "type": "composite",
                        "required": True,
                        "fields": [
                            {
                                "key": "aiexp.q12_other",
                                "label": "기타",
                                "type": "select",
                                "otherText": {
                                    "key": "aiexp.q12_other_txt",
                                    "label": "기타 대상",
                                },
                            }
                        ],
                    },
                ],
            }
        ],
    )

    import asyncio

    stored = asyncio.run(repository.create(definition))

    assert collection.document["audience"] == "elementary"
    assert (
        collection.document["spec"]["scales"][0]["questions"][0]["key"]
        == "demo.screening1"
    )
    assert collection.document["questions"] == [
        {
            "key": "demo.screening1",
            "csvColumn": "인구통계학적 정보 및 일반적 사항 | screening1 | 생성형 AI를 사용한 적이 있나요?",
            "order": 1,
        },
        {
            "key": "demo.age",
            "csvColumn": "인구통계학적 정보 및 일반적 사항 | 3 | 나이",
            "order": 2,
        },
        {
            "key": "usage.q11_1",
            "csvColumn": "인구통계학적 정보 및 일반적 사항 | 11 | 최근 한 달 생성형 AI 서비스별 사용 빈도 | ChatGPT",
            "order": 3,
        },
        {
            "key": "usage.q11_2",
            "csvColumn": "인구통계학적 정보 및 일반적 사항 | 11 | 최근 한 달 생성형 AI 서비스별 사용 빈도 | Gemini",
            "order": 4,
        },
        {
            "key": "usage.q16",
            "csvColumn": "인구통계학적 정보 및 일반적 사항 | 16 | AI 서비스 유료 결제 경험이 있나요?",
            "order": 5,
        },
        {
            "key": "usage.q16_amount",
            "csvColumn": "인구통계학적 정보 및 일반적 사항 | 16 | AI 서비스 유료 결제 경험이 있나요? | 월 결제 금액",
            "order": 6,
        },
        {
            "key": "usage.q19",
            "csvColumn": "인구통계학적 정보 및 일반적 사항 | 19 | 주로 사용하는 AI 서비스",
            "order": 7,
        },
        {
            "key": "usage.q19_other",
            "csvColumn": "인구통계학적 정보 및 일반적 사항 | 19 | 주로 사용하는 AI 서비스 | 기타 서비스명",
            "order": 8,
        },
        {
            "key": "aiexp.q12",
            "csvColumn": "인구통계학적 정보 및 일반적 사항 | 12 | 이야기 대상 순위",
            "order": 9,
        },
        {
            "key": "aiexp.q12_other",
            "csvColumn": "인구통계학적 정보 및 일반적 사항 | 12 | 이야기 대상 순위 | 기타",
            "order": 10,
        },
        {
            "key": "aiexp.q12_other_txt",
            "csvColumn": "인구통계학적 정보 및 일반적 사항 | 12 | 이야기 대상 순위 | 기타 대상",
            "order": 11,
        },
    ]
    assert stored.audience == "elementary"
    assert stored.spec["surveyVersion"] == "t1-elem-v1-draft"


def test_definition_model_reads_legacy_mongodb_snake_case_question_column() -> None:
    definition = SurveyDefinition(
        surveyRound=1,
        surveyVersion="demo-v1",
        questions=[{"key": "q1", "csv_column": "첫 번째 문항", "order": 1}],
        createdAt=datetime.now(UTC),
    )

    assert definition.questions[0].csv_column == "첫 번째 문항"


def test_mongo_definition_repository_lists_summaries() -> None:
    collection = InMemoryDefinitionCollection()
    repository = MongoSurveyDefinitionRepository(collection)
    definition = SurveyDefinitionCreate(
        surveyRound=1,
        surveyVersion="t1-elem-part1-v1-draft",
        audience="elementary",
        part=1,
        _meta={"title": "청소년 생성형 AI 사용 경험 연구 · 1회차 파트1 (초등)"},
        questions=[SurveyQuestion(key="q1", csvColumn="첫 번째 문항", order=1)],
    )

    import asyncio

    asyncio.run(repository.create(definition))
    summaries = asyncio.run(repository.list_definitions())

    assert summaries[0].survey_round == 1
    assert summaries[0].survey_version == "t1-elem-part1-v1-draft"
    assert summaries[0].audience == "elementary"
    assert summaries[0].part == 1
    assert summaries[0].title == "청소년 생성형 AI 사용 경험 연구 · 1회차 파트1 (초등)"
    assert summaries[0].question_count == 1

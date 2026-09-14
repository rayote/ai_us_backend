from datetime import UTC, datetime

from app.schemas.survey import SurveyDefinition, SurveyDefinitionCreate, SurveyQuestion, SurveyResponseRecord
from app.services.surveys import MongoSurveyDefinitionRepository, survey_responses_to_csv


class InMemoryDefinitionCollection:
    def __init__(self) -> None:
        self.document: dict[str, object] | None = None

    async def insert_one(self, document: dict[str, object]) -> None:
        self.document = document


def test_csv_export_uses_question_definition_order_and_preserves_missing_answers() -> None:
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
        == "아이디(휴대폰),학교급,학년,surveyRound,surveyVersion,submittedAt,첫 번째 문항,두 번째 문항"
    )
    assert csv_text.splitlines()[1].startswith('"=""01012345678""",초등,4,')
    assert csv_text.splitlines()[2].startswith('"=""01022223333""",중등,2,')
    assert csv_text.splitlines()[1].endswith("응답,선택 A; 선택 B")
    assert csv_text.splitlines()[2].endswith("다른 응답,")


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

    assert collection.document["questions"] == [{"key": "q1", "csvColumn": "첫 번째 문항", "order": 1}]
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
                        "detail": {"when": 2, "field": {"key": "usage.q16_amount", "label": "월 결제 금액"}},
                    },
                    {
                        "key": "usage.q19",
                        "no": "19",
                        "text": "주로 사용하는 AI 서비스",
                        "type": "single",
                        "required": True,
                        "other": {"when": 99, "field": {"key": "usage.q19_other", "label": "기타 서비스명"}},
                    },
                ],
            }
        ],
    )

    import asyncio

    stored = asyncio.run(repository.create(definition))

    assert collection.document["audience"] == "elementary"
    assert collection.document["spec"]["scales"][0]["questions"][0]["key"] == "demo.screening1"
    assert collection.document["questions"] == [
        {
            "key": "demo.screening1",
            "csvColumn": "인구통계학적 정보 및 일반적 사항 | screening1 | 생성형 AI를 사용한 적이 있나요?",
            "order": 1,
        },
        {"key": "demo.age", "csvColumn": "인구통계학적 정보 및 일반적 사항 | 3 | 나이", "order": 2},
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

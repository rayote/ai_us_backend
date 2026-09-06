from datetime import UTC, datetime

from app.schemas.survey import SurveyDefinition, SurveyQuestion, SurveyResponseRecord
from app.schemas.survey import SurveyDefinitionCreate
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

    csv_text = survey_responses_to_csv(definition, responses)

    assert csv_text.splitlines()[0] == "participantId,surveyRound,surveyVersion,submittedAt,첫 번째 문항,두 번째 문항"
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

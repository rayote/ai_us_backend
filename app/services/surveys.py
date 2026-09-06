from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from typing import Any, Protocol

from app.schemas.survey import SurveyDefinition, SurveyDefinitionCreate, SurveyResponseRecord
from pymongo.errors import DuplicateKeyError


class DuplicateSurveyDefinitionError(Exception):
    """Raised when a survey round and version already have a definition."""


class SurveyDefinitionRepository(Protocol):
    async def create(self, definition: SurveyDefinitionCreate) -> SurveyDefinition: ...

    async def get(self, survey_round: int, survey_version: str) -> SurveyDefinition | None: ...


class SurveyResponseRepository(Protocol):
    async def list_responses(self, survey_round: int, survey_version: str) -> list[SurveyResponseRecord]: ...


def _definition_from_document(document: dict[str, Any]) -> SurveyDefinition:
    return SurveyDefinition(
        surveyRound=document["survey_round"],
        surveyVersion=document["survey_version"],
        questions=document["questions"],
        createdAt=document["created_at"],
    )


def _response_from_document(document: dict[str, Any]) -> SurveyResponseRecord:
    return SurveyResponseRecord(
        participantId=str(document["participant_id"]),
        surveyRound=document["survey_round"],
        surveyVersion=document["survey_version"],
        answers=document["answers"],
        submittedAt=document["submitted_at"],
    )


class MongoSurveyDefinitionRepository:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def create(self, definition: SurveyDefinitionCreate) -> SurveyDefinition:
        document = {
            "survey_round": definition.survey_round,
            "survey_version": definition.survey_version,
            "questions": [question.model_dump() for question in definition.questions],
            "created_at": datetime.now(UTC),
        }
        try:
            await self._collection.insert_one(document)
        except DuplicateKeyError as error:
            raise DuplicateSurveyDefinitionError from error
        return _definition_from_document(document)

    async def get(self, survey_round: int, survey_version: str) -> SurveyDefinition | None:
        document = await self._collection.find_one(
            {"survey_round": survey_round, "survey_version": survey_version}
        )
        return _definition_from_document(document) if document else None


class MongoSurveyResponseRepository:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def list_responses(self, survey_round: int, survey_version: str) -> list[SurveyResponseRecord]:
        cursor = self._collection.find(
            {"survey_round": survey_round, "survey_version": survey_version}
        ).sort("submitted_at", 1)
        return [_response_from_document(document) async for document in cursor]


def survey_responses_to_csv(definition: SurveyDefinition, responses: list[SurveyResponseRecord]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    questions = sorted(definition.questions, key=lambda question: question.order)
    writer.writerow(
        ["participantId", "surveyRound", "surveyVersion", "submittedAt", *[question.csv_column for question in questions]]
    )
    for response in responses:
        writer.writerow(
            [
                response.participant_id,
                response.survey_round,
                response.survey_version,
                response.submitted_at.isoformat(),
                *[_csv_value(response.answers.get(question.key)) for question in questions],
            ]
        )
    return output.getvalue()


def _csv_value(value: object | None) -> object:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    return value
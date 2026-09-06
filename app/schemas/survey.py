from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SurveyQuestion(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    key: str = Field(min_length=1, max_length=100)
    csv_column: str = Field(alias="csvColumn", min_length=1, max_length=200)
    order: int = Field(ge=0)


class SurveyDefinitionCreate(BaseModel):
    survey_round: int = Field(alias="surveyRound", ge=1)
    survey_version: str = Field(alias="surveyVersion", min_length=1, max_length=100)
    questions: list[SurveyQuestion] = Field(min_length=1, max_length=1000)

    @field_validator("questions")
    @classmethod
    def require_unique_question_keys_and_orders(cls, questions: list[SurveyQuestion]) -> list[SurveyQuestion]:
        if len({question.key for question in questions}) != len(questions):
            raise ValueError("문항 키는 중복될 수 없습니다.")
        if len({question.order for question in questions}) != len(questions):
            raise ValueError("문항 순서는 중복될 수 없습니다.")
        return questions


class SurveyDefinition(BaseModel):
    survey_round: int = Field(alias="surveyRound")
    survey_version: str = Field(alias="surveyVersion")
    questions: list[SurveyQuestion]
    created_at: datetime = Field(alias="createdAt")


class SurveyResponseRecord(BaseModel):
    participant_id: str = Field(alias="participantId")
    survey_round: int = Field(alias="surveyRound")
    survey_version: str = Field(alias="surveyVersion")
    answers: dict[str, object]
    submitted_at: datetime = Field(alias="submittedAt")


class SurveySubmissionCreate(BaseModel):
    survey_round: int = Field(alias="surveyRound", ge=1)
    survey_version: str = Field(alias="surveyVersion", min_length=1, max_length=100)
    answers: dict[str, object]
    submission_id: str = Field(alias="submissionId", min_length=1, max_length=128)


class SurveySubmissionAccepted(BaseModel):
    submission_id: str = Field(alias="submissionId")
    status: str

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SurveyQuestion(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    key: str = Field(min_length=1, max_length=100)
    csv_column: str = Field(alias="csvColumn", min_length=1, max_length=500)
    order: int = Field(ge=0)


class SurveyDefinitionCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    survey_round: int = Field(alias="surveyRound", ge=1)
    survey_version: str = Field(alias="surveyVersion", min_length=1, max_length=100)
    audience: Literal["elementary", "secondary"] | None = None
    questions: list[SurveyQuestion] = Field(default_factory=list, max_length=1000)
    raw_spec: dict[str, Any] | None = Field(default=None, alias="rawSpec", exclude=True)

    @model_validator(mode="before")
    @classmethod
    def flatten_scale_questions(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        if data.get("questions"):
            if any(key in data for key in ("scales", "part", "_meta", "_reviewNotes")) and not data.get("rawSpec"):
                data = dict(data)
                data["rawSpec"] = deepcopy(data)
            return data
        scales = data.get("scales")
        if not isinstance(scales, list):
            return data
        data = dict(data)
        data["rawSpec"] = deepcopy(data)
        data["questions"] = _flatten_questions_from_scales(scales)
        return data

    @model_validator(mode="after")
    def require_unique_question_keys_and_orders(self) -> "SurveyDefinitionCreate":
        questions = self.questions
        if not questions:
            raise ValueError("설문 문항은 1개 이상이어야 합니다.")
        if len({question.key for question in questions}) != len(questions):
            raise ValueError("문항 키는 중복될 수 없습니다.")
        if len({question.order for question in questions}) != len(questions):
            raise ValueError("문항 순서는 중복될 수 없습니다.")
        return self


class SurveyDefinition(BaseModel):
    survey_round: int = Field(alias="surveyRound")
    survey_version: str = Field(alias="surveyVersion")
    audience: Literal["elementary", "secondary"] | None = None
    questions: list[SurveyQuestion]
    spec: dict[str, Any] | None = None
    created_at: datetime = Field(alias="createdAt")


class SurveyDefinitionSummary(BaseModel):
    survey_round: int = Field(alias="surveyRound")
    survey_version: str = Field(alias="surveyVersion")
    audience: Literal["elementary", "secondary"] | None = None
    part: int | None = None
    title: str | None = None
    question_count: int = Field(alias="questionCount")
    created_at: datetime = Field(alias="createdAt")


def _flatten_questions_from_scales(scales: list[object]) -> list[dict[str, object]]:
    questions: list[dict[str, object]] = []
    registered_keys: set[str] = set()

    def add_question(key: str, csv_column: str) -> None:
        normalized_key = key.strip()
        if not normalized_key or normalized_key in registered_keys:
            return
        registered_keys.add(normalized_key)
        questions.append({"key": normalized_key, "csvColumn": csv_column, "order": len(questions) + 1})

    for scale in scales:
        if not isinstance(scale, dict):
            continue
        scale_name = str(scale.get("scaleName") or scale.get("scaleId") or "").strip()
        scale_questions = scale.get("questions")
        if not isinstance(scale_questions, list):
            continue
        for question in scale_questions:
            if not isinstance(question, dict):
                continue
            key = str(question.get("key") or "").strip()
            if not key:
                continue
            csv_column = _csv_column_from_question(scale_name, question)
            if question.get("type") == "grid" and isinstance(question.get("rows"), list):
                for row in question["rows"]:
                    if isinstance(row, dict):
                        row_key = str(row.get("key") or "").strip()
                        row_label = str(row.get("text") or row.get("label") or row_key).strip()
                        add_question(row_key, f"{csv_column} | {row_label}")
            else:
                add_question(key, csv_column)
            for extra_field in _extra_answer_fields(question):
                extra_key = str(extra_field.get("key") or "").strip()
                extra_label = str(extra_field.get("label") or extra_key).strip()
                add_question(extra_key, f"{csv_column} | {extra_label}")
    return questions


def _extra_answer_fields(question: dict[str, object]) -> list[dict[str, object]]:
    fields: list[dict[str, object]] = []
    composite_fields = question.get("fields")
    if question.get("type") == "composite" and isinstance(composite_fields, list):
        fields.extend(field for field in composite_fields if isinstance(field, dict))
    for container_name in ("detail", "other"):
        container = question.get(container_name)
        if isinstance(container, dict) and isinstance(container.get("field"), dict):
            fields.append(container["field"])
    return fields


def _csv_column_from_question(scale_name: str, question: dict[str, object]) -> str:
    explicit_column = str(question.get("csvColumn") or question.get("csv_column") or "").strip()
    if explicit_column:
        return explicit_column
    number = str(question.get("no") or "").strip()
    text = str(question.get("text") or question.get("key") or "").strip()
    pieces = [piece for piece in (scale_name, number, text) if piece]
    return " | ".join(pieces)


class SurveyResponseRecord(BaseModel):
    participant_id: str = Field(alias="participantId")
    survey_round: int = Field(alias="surveyRound")
    survey_version: str = Field(alias="surveyVersion")
    answers: dict[str, object]
    submitted_at: datetime = Field(alias="submittedAt")


class SurveyResponsePreview(BaseModel):
    phone: str
    school_level: str | None = Field(alias="schoolLevel")
    grade: int | None = None
    survey_round: int = Field(alias="surveyRound")
    survey_version: str = Field(alias="surveyVersion")
    submitted_at: datetime = Field(alias="submittedAt")


class SurveySubmissionCreate(BaseModel):
    survey_round: int = Field(alias="surveyRound", ge=1)
    survey_version: str = Field(alias="surveyVersion", min_length=1, max_length=100)
    answers: dict[str, object]
    submission_id: str = Field(alias="submissionId", min_length=1, max_length=128)


class SurveySubmissionAccepted(BaseModel):
    submission_id: str = Field(alias="submissionId")
    status: str

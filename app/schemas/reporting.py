from __future__ import annotations

from pydantic import BaseModel, Field


class SchoolLevelCount(BaseModel):
    elementary: int
    middle: int
    high: int
    total: int


class SurveyRoundCount(BaseModel):
    survey_round: int = Field(alias="surveyRound")
    completed_count: int = Field(alias="completedCount")


class ParticipationStatus(BaseModel):
    participants: SchoolLevelCount
    completed_by_round: list[SurveyRoundCount] = Field(alias="completedByRound")


class NonparticipantRecord(BaseModel):
    participant_id: str = Field(alias="participantId")
    name: str | None = None
    school_level: str | None = Field(alias="schoolLevel")
    grade: int | None = None
    phone: str


class NonparticipantReport(BaseModel):
    survey_round: int = Field(alias="surveyRound")
    survey_version: str = Field(alias="surveyVersion")
    counts: SchoolLevelCount
    participants: list[NonparticipantRecord]
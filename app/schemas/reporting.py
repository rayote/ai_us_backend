from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SchoolLevelCount(BaseModel):
    elementary: int
    middle: int
    high: int
    total: int


class SurveyRoundCount(BaseModel):
    survey_round: int = Field(alias="surveyRound")
    completed_count: int = Field(alias="completedCount")
    counts: SchoolLevelCount


class ChatParticipationRecord(BaseModel):
    participant_id: str = Field(alias="participantId")
    name: str | None = None
    school_level: str | None = Field(alias="schoolLevel")
    grade: int | None = None
    phone: str
    chat_consent: bool = Field(alias="chatConsent")
    after_round_1: Literal["not_submitted", "active", "deletion_requested"] = Field(alias="afterRound1")
    after_round_4: Literal["not_submitted", "active", "deletion_requested"] = Field(alias="afterRound4")


class ChatParticipationStatus(BaseModel):
    consented: SchoolLevelCount
    submitted_after_round_1: SchoolLevelCount = Field(alias="submittedAfterRound1")
    submitted_after_round_4: SchoolLevelCount = Field(alias="submittedAfterRound4")
    participants: list[ChatParticipationRecord]


class ParticipationStatus(BaseModel):
    participants: SchoolLevelCount
    completed_by_round: list[SurveyRoundCount] = Field(alias="completedByRound")
    chat: ChatParticipationStatus


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


class IncompleteParticipantReport(BaseModel):
    category: Literal["survey", "chat"]
    criterion: str
    counts: SchoolLevelCount
    participants: list[NonparticipantRecord]

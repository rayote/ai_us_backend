from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)


class ApplicationConsents(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    document_read: Literal[True] = Field(alias="documentRead")
    survey: Literal[True]
    chat: bool
    participant: Literal[True]
    guardian: Literal[True]


class ApplicationCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    gender: str = Field(min_length=1, max_length=20)
    grade: str = Field(min_length=1, max_length=50)
    phone: str
    guardian_phone: str = Field(alias="guardianPhone")
    email: str | None = Field(default=None, max_length=254)
    sns: str | None = Field(default=None, max_length=200)
    consents: ApplicationConsents

    @field_validator("phone", "guardian_phone", mode="before")
    @classmethod
    def normalize_phone_number(cls, value: object) -> str:
        normalized = re.sub(r"\D", "", str(value))
        if not re.fullmatch(r"01\d{9}", normalized):
            raise ValueError("휴대폰 번호는 숫자 11자리여야 합니다.")
        return normalized

    @model_validator(mode="after")
    def require_distinct_participant_and_guardian_phones(self) -> "ApplicationCreate":
        if self.phone == self.guardian_phone:
            raise ValueError(
                "본인 휴대폰 번호와 보호자 휴대폰 번호는 서로 달라야 합니다."
            )
        return self

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            raise ValueError("올바른 이메일 주소가 아닙니다.")
        return normalized


class ApplicationCreated(BaseModel):
    application_id: str = Field(alias="applicationId")
    status: Literal["pending", "approved"]
    access_token: str | None = Field(default=None, alias="accessToken")
    token_type: Literal["bearer"] | None = Field(default=None, alias="tokenType")
    role: Literal["participant"] | None = None
    needs_password_change: bool | None = Field(
        default=None, alias="needsPasswordChange"
    )
    audience: Literal["elementary", "secondary"] | None = None
    chat_consent: bool | None = Field(default=None, alias="chatConsent")
    participant_phone: str | None = Field(default=None, alias="participantPhone")
    guardian_phone: str | None = Field(default=None, alias="guardianPhone")
    school_level: str | None = Field(default=None, alias="schoolLevel")
    grade: int | None = None
    email: str | None = None
    sns: str | None = None


class ApplicationSettings(BaseModel):
    auto_approval: bool = Field(alias="autoApproval")


class ApplicationSettingsUpdate(ApplicationSettings):
    pass


class SurveyConfiguration(BaseModel):
    id: str
    label: str
    versions: list[dict[str, str | int]]


class SurveyRoundSetting(BaseModel):
    survey_round: Literal[1, 2, 3, 4] = Field(alias="surveyRound")
    is_open: StrictBool = Field(alias="isOpen")
    configuration_id: str | None = Field(alias="configurationId")
    submission_allowed: bool = Field(alias="submissionAllowed")
    period_ended: bool = Field(alias="periodEnded")
    state: Literal["preparing", "open", "closed"]
    configurations: list[SurveyConfiguration]
    updated_at: datetime | None = Field(default=None, alias="updatedAt")
    updated_by: str | None = Field(default=None, alias="updatedBy")

    @classmethod
    def from_record(cls, survey_round: int, record: dict) -> "SurveyRoundSetting":
        return cls(
            surveyRound=survey_round,
            isOpen=record["is_open"],
            configurationId=record["configuration_id"],
            submissionAllowed=record["submission_allowed"],
            periodEnded=record["period_ended"],
            state=record["state"],
            configurations=record["configurations"],
            updatedAt=record.get("updated_at"),
            updatedBy=record.get("updated_by"),
        )


class SurveyRoundSettingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_open: StrictBool | None = Field(default=None, alias="isOpen")
    configuration_id: str | None = Field(
        default=None, alias="configurationId", min_length=1
    )
    period_ended: StrictBool | None = Field(default=None, alias="periodEnded")

    @model_validator(mode="after")
    def require_one_change(self) -> "SurveyRoundSettingUpdate":
        if len(self.model_fields_set) != 1:
            raise ValueError("설정은 한 항목씩 변경해 주세요.")
        if any(
            getattr(self, key) is None
            for key in self.model_fields_set
            if key != "configuration_id"
        ):
            raise ValueError("토글 값은 true 또는 false여야 합니다.")
        return self


class ChatConsentUpdate(BaseModel):
    chat_consent: bool = Field(alias="chatConsent", strict=True)
    expected_revision: int = Field(alias="expectedRevision", ge=0, strict=True)
    restore_existing: bool = Field(default=False, alias="restoreExisting", strict=True)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_change(self) -> ChatConsentUpdate:
        self.reason = self.reason.strip()
        if not self.reason:
            raise ValueError("변경 사유를 입력해 주세요.")
        if self.restore_existing and not self.chat_consent:
            raise ValueError("기존 자료 복원에는 대화문 동의가 필요합니다.")
        return self


class ChatConsentLookup(BaseModel):
    phone: str = Field(pattern=r"^01[0-9]{9}$")


class ApplicationRecord(BaseModel):
    application_id: str = Field(alias="applicationId")
    gender: str
    grade: str
    phone: str
    guardian_phone: str = Field(alias="guardianPhone")
    email: str | None = None
    sns: str | None = None
    consents: ApplicationConsents
    chat_consent: bool | None = Field(default=None, alias="chatConsent")
    status: Literal["pending", "approved"]
    submitted_at: datetime = Field(alias="submittedAt")
    approved_at: datetime | None = Field(default=None, alias="approvedAt")


class ApplicationApproval(BaseModel):
    application_ids: list[str] = Field(
        alias="applicationIds", min_length=1, max_length=100
    )

    @field_validator("application_ids")
    @classmethod
    def validate_application_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("신청 ID는 중복될 수 없습니다.")
        if any(
            not re.fullmatch(r"[0-9a-fA-F]{24}", application_id)
            for application_id in value
        ):
            raise ValueError("올바른 신청 ID가 아닙니다.")
        return value


class ApplicationApprovalCompleted(BaseModel):
    approved_count: int = Field(alias="approvedCount")

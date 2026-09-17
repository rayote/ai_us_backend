from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ParticipantLogin(BaseModel):
    phone: str
    password: str = Field(min_length=1, max_length=256)
    audience: Literal["elementary", "secondary"]

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone_number(cls, value: object) -> str:
        if value is None:
            return value
        normalized = re.sub(r"\D", "", str(value))
        if not re.fullmatch(r"01\d{9}", normalized):
            raise ValueError("휴대폰 번호는 숫자 11자리여야 합니다.")
        return normalized


class ResearcherLogin(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


class ResearcherCreate(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=256)
    role: Literal["researcher"] = "researcher"


class ResearcherCreated(BaseModel):
    researcher_id: str = Field(alias="researcherId")
    username: str
    role: Literal["researcher"]


class PasswordChange(BaseModel):
    current_password: str = Field(alias="currentPassword", min_length=1, max_length=256)
    new_password: str = Field(alias="newPassword", min_length=8, max_length=256)

    @field_validator("new_password")
    @classmethod
    def prevent_default_password_reuse(cls, value: str) -> str:
        if value == "1234":
            raise ValueError("초기 비밀번호는 새 비밀번호로 사용할 수 없습니다.")
        return value


class AccessToken(BaseModel):
    access_token: str = Field(alias="accessToken")
    token_type: Literal["bearer"] = Field(alias="tokenType")
    role: Literal["participant", "researcher", "admin"]
    needs_password_change: bool = Field(alias="needsPasswordChange")
    audience: Literal["elementary", "secondary"] | None = None
    requested_audience: Literal["elementary", "secondary"] | None = Field(default=None, alias="requestedAudience")
    audience_switched: bool | None = Field(default=None, alias="audienceSwitched")
    chat_consent: bool | None = Field(default=None, alias="chatConsent")
    username: str | None = None


class PasswordChangeCompleted(BaseModel):
    status: Literal["completed"]


class PasswordResetRequest(BaseModel):
    phone: str
    guardian_phone: str | None = Field(default=None, alias="guardianPhone")
    email: str | None = None

    @field_validator("phone", "guardian_phone", mode="before")
    @classmethod
    def normalize_phone_number(cls, value: object) -> str:
        if value is None:
            return value
        normalized = re.sub(r"\D", "", str(value))
        if not re.fullmatch(r"01\d{9}", normalized):
            raise ValueError("휴대폰 번호는 숫자 11자리여야 합니다.")
        return normalized

    @field_validator("email")
    @classmethod
    def normalize_legacy_email(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            raise ValueError("올바른 이메일 주소가 아닙니다.")
        return normalized


class PasswordResetCompleted(BaseModel):
    status: Literal["completed"]

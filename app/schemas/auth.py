from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ParticipantLogin(BaseModel):
    phone: str
    password: str = Field(min_length=1, max_length=256)

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone_number(cls, value: object) -> str:
        normalized = re.sub(r"\D", "", str(value))
        if not re.fullmatch(r"01\d{9}", normalized):
            raise ValueError("휴대폰 번호는 숫자 11자리여야 합니다.")
        return normalized


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
    role: Literal["participant"]
    needs_password_change: bool = Field(alias="needsPasswordChange")


class PasswordChangeCompleted(BaseModel):
    status: Literal["completed"]
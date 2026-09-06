from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    email: str = Field(min_length=3, max_length=254)
    consents: ApplicationConsents

    @field_validator("phone", "guardian_phone", mode="before")
    @classmethod
    def normalize_phone_number(cls, value: object) -> str:
        normalized = re.sub(r"\D", "", str(value))
        if not re.fullmatch(r"01\d{9}", normalized):
            raise ValueError("휴대폰 번호는 숫자 11자리여야 합니다.")
        return normalized

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            raise ValueError("올바른 이메일 주소가 아닙니다.")
        return normalized


class ApplicationCreated(BaseModel):
    application_id: str = Field(alias="applicationId")
    status: Literal["pending"]

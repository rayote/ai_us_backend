from __future__ import annotations

import csv
import io
import re

from app.core.security import hash_password
from app.schemas.imports import ImportErrorRecord, ParticipantImportResult
from app.services.auth import ParticipantAccountRepository

_REQUIRED_HEADERS = ("이름", "휴대폰번호", "학교급", "학년")
_SCHOOL_LEVELS = {"초등", "중등", "고등"}


class ParticipantImportService:
    def __init__(self, participants: ParticipantAccountRepository) -> None:
        self._participants = participants

    async def import_csv(self, content: bytes) -> ParticipantImportResult:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            return ParticipantImportResult(
                createdCount=0,
                skippedCount=0,
                errors=[ImportErrorRecord(row=0, message="CSV 파일은 UTF-8 형식이어야 합니다.")],
            )
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None or not set(_REQUIRED_HEADERS).issubset(reader.fieldnames):
            return ParticipantImportResult(
                createdCount=0,
                skippedCount=0,
                errors=[ImportErrorRecord(row=0, message="필수 열: 이름, 휴대폰번호, 학교급, 학년")],
            )

        created_count = 0
        skipped_count = 0
        errors: list[ImportErrorRecord] = []
        for row_number, row in enumerate(reader, start=2):
            try:
                name, phone, school_level, grade = self._validate_row(row)
            except ValueError as error:
                errors.append(ImportErrorRecord(row=row_number, message=str(error)))
                continue
            created = await self._participants.create_imported(
                phone,
                hash_password("1234"),
                name,
                school_level,
                grade,
            )
            if created:
                created_count += 1
            else:
                skipped_count += 1
        return ParticipantImportResult(
            createdCount=created_count,
            skippedCount=skipped_count,
            errors=errors,
        )

    @staticmethod
    def _validate_row(row: dict[str, str | None]) -> tuple[str, str, str, int]:
        name = (row.get("이름") or "").strip()
        phone = re.sub(r"\D", "", row.get("휴대폰번호") or "")
        school_level = (row.get("학교급") or "").strip()
        grade_text = (row.get("학년") or "").strip()
        if not name:
            raise ValueError("이름이 비어 있습니다.")
        if not re.fullmatch(r"01\d{9}", phone):
            raise ValueError("휴대폰번호는 숫자 11자리여야 합니다.")
        if school_level not in _SCHOOL_LEVELS:
            raise ValueError("학교급은 초등, 중등, 고등 중 하나여야 합니다.")
        if not grade_text.isdigit() or not 1 <= int(grade_text) <= 6:
            raise ValueError("학년은 1에서 6 사이의 숫자여야 합니다.")
        return name, phone, school_level, int(grade_text)

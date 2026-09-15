import asyncio

from app.services.auth import ParticipantAccountRepository
from app.services.imports import ParticipantImportService


class InMemoryParticipantAccounts(ParticipantAccountRepository):
    def __init__(self) -> None:
        self.imported: dict[str, tuple[str, str, int, str | None, str | None, str | None]] = {}

    async def find_by_phone(self, phone: str):
        return None

    async def find_by_id(self, participant_id: str):
        return None

    async def update_password(self, participant_id: str, password_hash: str) -> bool:
        return False

    async def reset_password_by_phone_email(self, phone: str, email: str, password_hash: str) -> bool:
        return False

    async def create(
        self,
        phone: str,
        password_hash: str,
        chat_consent: bool = False,
        school_level: str | None = None,
        email: str | None = None,
    ) -> bool:
        return False

    async def create_imported(
        self,
        phone: str,
        password_hash: str,
        name: str,
        school_level: str,
        grade: int,
        email: str | None = None,
        sns: str | None = None,
        guardian_phone: str | None = None,
    ) -> bool:
        if phone in self.imported:
            return False
        self.imported[phone] = (name, school_level, grade, email, sns, guardian_phone)
        return True


def test_import_creates_valid_rows_and_reports_invalid_rows() -> None:
    repository = InMemoryParticipantAccounts()
    service = ParticipantImportService(repository)

    result = asyncio.run(
        service.import_csv(
            "이름,휴대폰번호,보호자휴대폰,학교급,학년,이메일\n"
            "홍길동,010-1234-5678,010-9999-9999,초등,4,user@example.com\n"
            ",010-8888-8888,010-7777-7777,중등,2,missing@example.com\n".encode()
        )
    )

    assert result.created_count == 1
    assert result.skipped_count == 0
    assert result.errors[0].row == 3
    assert repository.imported["01012345678"] == ("홍길동", "초등", 4, "user@example.com", None, "01099999999")


def test_import_allows_missing_optional_contact_columns() -> None:
    service = ParticipantImportService(InMemoryParticipantAccounts())

    result = asyncio.run(
        service.import_csv(
            "이름,휴대폰번호,보호자휴대폰,학교급,학년\n홍길동,010-1234-5678,010-9999-9999,초등,4\n".encode()
        )
    )

    assert result.created_count == 1


def test_import_accepts_required_email_column_for_password_reset() -> None:
    repository = InMemoryParticipantAccounts()
    service = ParticipantImportService(repository)

    result = asyncio.run(
        service.import_csv(
            "이름,휴대폰번호,보호자휴대폰,학교급,학년,이메일\n홍길동,010-1234-5678,010-9999-9999,초등,4,USER@Example.com\n".encode()
        )
    )

    assert result.created_count == 1
    assert repository.imported["01012345678"] == ("홍길동", "초등", 4, "user@example.com", None, "01099999999")


def test_import_skips_existing_phone_number() -> None:
    repository = InMemoryParticipantAccounts()
    service = ParticipantImportService(repository)
    csv_content = "이름,휴대폰번호,보호자휴대폰,학교급,학년,이메일\n홍길동,010-1234-5678,010-9999-9999,초등,4,user@example.com\n".encode()

    first_result = asyncio.run(service.import_csv(csv_content))
    second_result = asyncio.run(service.import_csv(csv_content))

    assert first_result.created_count == 1
    assert second_result.created_count == 0
    assert second_result.skipped_count == 1

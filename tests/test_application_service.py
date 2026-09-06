import asyncio
from datetime import UTC, datetime

from app.schemas.application import ApplicationConsents, ApplicationRecord
from app.services.applications import ApplicationRepository


class InMemoryApplicationRepository(ApplicationRepository):
    def __init__(self) -> None:
        self.applications = [
            ApplicationRecord(
                applicationId="1",
                gender="여",
                grade="초등학교 5학년",
                phone="01012345678",
                guardianPhone="01099999999",
                email="elementary@example.com",
                consents=ApplicationConsents(
                    documentRead=True, survey=True, chat=False, participant=True, guardian=True
                ),
                status="pending",
                submittedAt=datetime.now(UTC),
            ),
            ApplicationRecord(
                applicationId="2",
                gender="남",
                grade="중학교 2학년",
                phone="01087654321",
                guardianPhone="01088888888",
                email="middle@example.com",
                consents=ApplicationConsents(
                    documentRead=True, survey=True, chat=True, participant=True, guardian=True
                ),
                status="pending",
                submittedAt=datetime.now(UTC),
            ),
        ]

    async def create(self, application: object) -> str:
        raise NotImplementedError

    async def list_applications(self, school_level: str | None = None) -> list[ApplicationRecord]:
        prefixes = {"elementary": "초등", "middle": "중학", "high": "고등"}
        if school_level is None:
            return self.applications
        return [record for record in self.applications if record.grade.startswith(prefixes[school_level])]

    async def approve(self, application_ids: list[str]) -> int:
        approved = 0
        for index, application in enumerate(self.applications):
            if application.application_id in application_ids and application.status == "pending":
                self.applications[index] = application.model_copy(
                    update={"status": "approved", "approved_at": datetime.now(UTC)}
                )
                approved += 1
        return approved


def test_list_filters_applications_by_school_level() -> None:
    repository = InMemoryApplicationRepository()

    records = asyncio.run(repository.list_applications("elementary"))

    assert [record.application_id for record in records] == ["1"]


def test_approve_updates_pending_applications_only() -> None:
    repository = InMemoryApplicationRepository()

    approved_count = asyncio.run(repository.approve(["1", "2"]))
    duplicate_approved_count = asyncio.run(repository.approve(["1"]))

    assert approved_count == 2
    assert duplicate_approved_count == 0
    assert all(record.status == "approved" for record in repository.applications)
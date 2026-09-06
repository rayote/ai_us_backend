from __future__ import annotations

from app.core.security import hash_password
from app.services.applications import ApplicationRepository
from app.services.auth import ParticipantAccountRepository


class ExistingParticipantError(Exception):
    def __init__(self, phone_numbers: list[str]) -> None:
        self.phone_numbers = phone_numbers


class ApplicationApprovalService:
    def __init__(
        self,
        application_repository: ApplicationRepository,
        participant_repository: ParticipantAccountRepository,
    ) -> None:
        self._application_repository = application_repository
        self._participant_repository = participant_repository

    async def approve(self, application_ids: list[str]) -> int:
        pending_applications = await self._application_repository.get_pending(application_ids)
        existing_phone_numbers = [
            application.phone
            for application in pending_applications
            if await self._participant_repository.find_by_phone(application.phone) is not None
        ]
        if existing_phone_numbers:
            raise ExistingParticipantError(existing_phone_numbers)
        for application in pending_applications:
            created = await self._participant_repository.create(
                application.phone,
                hash_password("1234"),
                application.consents.chat,
                (
                    "초등"
                    if application.grade.startswith("초등")
                    else "중등" if application.grade.startswith("중학") else "고등"
                ),
            )
            if not created:
                raise ExistingParticipantError([application.phone])
        return await self._application_repository.approve(
            [application.application_id for application in pending_applications]
        )

from __future__ import annotations

from app.core.security import hash_password
from app.services.applications import ApplicationRepository
from app.services.auth import ParticipantAccountRepository


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
        for application in pending_applications:
            await self._participant_repository.create(
                application.phone,
                hash_password("1234"),
                application.consents.chat,
                "초등" if application.grade.startswith("초등") else "중등" if application.grade.startswith("중학") else "고등",
            )
        return await self._application_repository.approve(
            [application.application_id for application in pending_applications]
        )

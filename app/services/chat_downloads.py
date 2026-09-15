from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ChatDownloadArtifact(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    job_id: str = Field(alias="jobId")
    file_id: str = Field(alias="fileId")
    filename: str
    size: int
    created_at: datetime = Field(alias="createdAt")


class ChatDownloadArtifactRepository(Protocol):
    async def create(self, artifact: ChatDownloadArtifact) -> None:
        ...

    async def get(self, job_id: str) -> ChatDownloadArtifact | None:
        ...


class MongoChatDownloadArtifactRepository:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def create(self, artifact: ChatDownloadArtifact) -> None:
        await self._collection.insert_one(artifact.model_dump(by_alias=False))

    async def get(self, job_id: str) -> ChatDownloadArtifact | None:
        document = await self._collection.find_one({"job_id": job_id})
        return ChatDownloadArtifact.model_validate(document) if document else None


def new_artifact(job_id: str, file_id: str, filename: str, size: int) -> ChatDownloadArtifact:
    return ChatDownloadArtifact(
        jobId=job_id,
        fileId=file_id,
        filename=filename,
        size=size,
        createdAt=datetime.now(UTC),
    )

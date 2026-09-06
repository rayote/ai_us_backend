import asyncio
from datetime import UTC, datetime

from app.schemas.jobs import Job, JobCreate
from app.services.jobs import JobRepository, QueueWorker


class InMemoryJobRepository(JobRepository):
    def __init__(self) -> None:
        self.jobs: list[Job] = []

    async def enqueue(self, job: JobCreate) -> Job:
        for existing_job in self.jobs:
            if existing_job.job_type == job.job_type and existing_job.idempotency_key == job.idempotency_key:
                return existing_job
        queued_job = Job(
            id=str(len(self.jobs) + 1),
            job_type=job.job_type,
            idempotency_key=job.idempotency_key,
            payload=job.payload,
            status="queued",
            attempts=0,
            created_at=datetime.now(UTC),
        )
        self.jobs.append(queued_job)
        return queued_job

    async def claim_next(self) -> Job | None:
        for index, job in enumerate(self.jobs):
            if job.status == "queued":
                processing_job = job.model_copy(update={"status": "processing", "attempts": job.attempts + 1})
                self.jobs[index] = processing_job
                return processing_job
        return None

    async def recover_interrupted(self) -> int:
        recovered = 0
        for index, job in enumerate(self.jobs):
            if job.status == "processing":
                self.jobs[index] = job.model_copy(
                    update={"status": "queued", "error": "Worker restarted before this job completed."}
                )
                recovered += 1
        return recovered

    async def complete(self, job_id: str) -> None:
        self._update(job_id, status="completed", processed_at=datetime.now(UTC))

    async def retry(self, job_id: str, error: str) -> None:
        self._update(job_id, status="queued", error=error)

    async def fail(self, job_id: str, error: str) -> None:
        self._update(job_id, status="failed", processed_at=datetime.now(UTC), error=error)

    def _update(self, job_id: str, **changes: object) -> None:
        for index, job in enumerate(self.jobs):
            if job.id == job_id:
                self.jobs[index] = job.model_copy(update=changes)
                return
        raise AssertionError(f"Unknown job: {job_id}")


def test_worker_completes_queued_job() -> None:
    asyncio.run(_worker_completes_queued_job())


async def _worker_completes_queued_job() -> None:
    repository = InMemoryJobRepository()
    job = await repository.enqueue(
        JobCreate(job_type="survey_response", idempotency_key="submission-1", payload={"wave": 1})
    )
    received_payloads: list[dict[str, int]] = []

    async def handle_survey(payload: dict[str, int]) -> None:
        received_payloads.append(payload)

    worker = QueueWorker(repository, {"survey_response": handle_survey})

    assert await worker.process_one() is True
    assert received_payloads == [{"wave": 1}]
    assert repository.jobs[0].id == job.id
    assert repository.jobs[0].status == "completed"
    assert repository.jobs[0].attempts == 1


def test_enqueue_returns_existing_job_for_same_idempotency_key() -> None:
    asyncio.run(_enqueue_returns_existing_job_for_same_idempotency_key())


async def _enqueue_returns_existing_job_for_same_idempotency_key() -> None:
    repository = InMemoryJobRepository()
    initial_job = await repository.enqueue(
        JobCreate(job_type="survey_response", idempotency_key="submission-1", payload={"wave": 1})
    )
    duplicate_job = await repository.enqueue(
        JobCreate(job_type="survey_response", idempotency_key="submission-1", payload={"wave": 1})
    )

    assert duplicate_job.id == initial_job.id
    assert len(repository.jobs) == 1


def test_worker_marks_unhandled_job_as_failed() -> None:
    asyncio.run(_worker_marks_unhandled_job_as_failed())


async def _worker_marks_unhandled_job_as_failed() -> None:
    repository = InMemoryJobRepository()
    await repository.enqueue(
        JobCreate(job_type="chat_submission", idempotency_key="submission-2", payload={"wave": 1})
    )

    worker = QueueWorker(repository, {})

    assert await worker.process_one() is True
    assert repository.jobs[0].status == "failed"


def test_worker_retries_a_failed_job_before_marking_it_failed() -> None:
    asyncio.run(_worker_retries_a_failed_job_before_marking_it_failed())


async def _worker_retries_a_failed_job_before_marking_it_failed() -> None:
    repository = InMemoryJobRepository()
    await repository.enqueue(
        JobCreate(job_type="survey_response", idempotency_key="submission-3", payload={"wave": 1})
    )

    async def fail_survey(_: dict[str, int]) -> None:
        raise RuntimeError("temporary MongoDB error")

    worker = QueueWorker(repository, {"survey_response": fail_survey}, max_attempts=2)

    assert await worker.process_one() is True
    assert repository.jobs[0].status == "queued"
    assert repository.jobs[0].attempts == 1

    assert await worker.process_one() is True
    assert repository.jobs[0].status == "failed"
    assert repository.jobs[0].attempts == 2


def test_worker_recovers_interrupted_processing_job() -> None:
    asyncio.run(_worker_recovers_interrupted_processing_job())


async def _worker_recovers_interrupted_processing_job() -> None:
    repository = InMemoryJobRepository()
    await repository.enqueue(
        JobCreate(job_type="survey_response", idempotency_key="submission-4", payload={"wave": 1})
    )
    await repository.claim_next()
    worker = QueueWorker(repository, {})

    assert await worker.recover() == 1
    assert repository.jobs[0].status == "queued"

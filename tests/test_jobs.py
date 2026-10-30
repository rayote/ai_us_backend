import asyncio
from datetime import UTC, datetime

from app.schemas.jobs import Job, JobCreate
from app.services.jobs import JobRepository, QueueWorker


def test_analysis_worker_heartbeat_is_independent_of_submission_worker() -> None:
    from app.services.worker_health import MongoWorkerHealthRepository

    class Heartbeats:
        def __init__(self):
            self.documents = {}

        async def update_one(self, query, update, **kwargs):
            self.documents[query["_id"]] = update["$set"]

        async def find_one(self, query):
            return self.documents.get(query["_id"])

    async def check():
        collection = Heartbeats()
        submission = MongoWorkerHealthRepository(collection)
        analysis = MongoWorkerHealthRepository(collection, "analysis-worker")
        timestamp = datetime.now(UTC)
        await submission.touch(timestamp)
        assert await analysis.last_heartbeat() is None
        await analysis.touch(timestamp)
        assert await submission.last_heartbeat() == timestamp
        assert set(collection.documents) == {"queue-worker", "analysis-worker"}
        assert (
            JobCreate(
                job_type="abuse_review", idempotency_key="review", payload={}
            ).job_type
            == "abuse_review"
        )

    asyncio.run(check())


class InMemoryJobRepository(JobRepository):
    def __init__(self) -> None:
        self.jobs: list[Job] = []

    async def enqueue(self, job: JobCreate) -> Job:
        for existing_job in self.jobs:
            if (
                existing_job.job_type == job.job_type
                and existing_job.idempotency_key == job.idempotency_key
            ):
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

    async def get(self, job_id: str) -> Job | None:
        return next((job for job in self.jobs if job.id == job_id), None)

    async def claim_next(self) -> Job | None:
        for index, job in enumerate(self.jobs):
            if job.status == "queued":
                processing_job = job.model_copy(
                    update={"status": "processing", "attempts": job.attempts + 1}
                )
                self.jobs[index] = processing_job
                return processing_job
        return None

    async def recover_interrupted(self) -> int:
        recovered = 0
        for index, job in enumerate(self.jobs):
            if job.status == "processing":
                self.jobs[index] = job.model_copy(
                    update={
                        "status": "queued",
                        "error": "Worker restarted before this job completed.",
                    }
                )
                recovered += 1
        return recovered

    async def complete(self, job_id: str) -> None:
        self._update(job_id, status="completed", processed_at=datetime.now(UTC))

    async def retry(self, job_id: str, error: str) -> None:
        self._update(job_id, status="queued", error=error)

    async def fail(self, job_id: str, error: str) -> None:
        self._update(
            job_id, status="failed", processed_at=datetime.now(UTC), error=error
        )

    def _update(self, job_id: str, **changes: object) -> None:
        for index, job in enumerate(self.jobs):
            if job.id == job_id:
                self.jobs[index] = job.model_copy(update=changes)
                return
        raise AssertionError(f"Unknown job: {job_id}")


def test_worker_completes_queued_job() -> None:
    asyncio.run(_worker_completes_queued_job())


def test_analysis_calculation_does_not_block_heartbeat_or_other_coroutines() -> None:
    import threading

    from app.analysis_worker import calculate_report

    async def check():
        entered = threading.Event()
        release = threading.Event()

        class Analysis:
            async def report(self, *args, **kwargs):
                assert kwargs["include_all_candidates"] is True
                entered.set()
                assert release.wait(3)
                return "report"

        class Results:
            async def store_report(self, job_id, report):
                assert (job_id, report) == ("analysis", "report")

        task = asyncio.create_task(
            calculate_report(Analysis(), Results(), {"jobId": "analysis"})
        )
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert not task.done()
            release.set()
            await asyncio.wait_for(task, 2)
        finally:
            release.set()

    asyncio.run(check())


async def _worker_completes_queued_job() -> None:
    repository = InMemoryJobRepository()
    job = await repository.enqueue(
        JobCreate(
            job_type="survey_response",
            idempotency_key="submission-1",
            payload={"wave": 1},
        )
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
        JobCreate(
            job_type="survey_response",
            idempotency_key="submission-1",
            payload={"wave": 1},
        )
    )
    duplicate_job = await repository.enqueue(
        JobCreate(
            job_type="survey_response",
            idempotency_key="submission-1",
            payload={"wave": 1},
        )
    )

    assert duplicate_job.id == initial_job.id
    assert len(repository.jobs) == 1


def test_worker_marks_unhandled_job_as_failed() -> None:
    asyncio.run(_worker_marks_unhandled_job_as_failed())


async def _worker_marks_unhandled_job_as_failed() -> None:
    repository = InMemoryJobRepository()
    await repository.enqueue(
        JobCreate(
            job_type="chat_submission",
            idempotency_key="submission-2",
            payload={"wave": 1},
        )
    )

    worker = QueueWorker(repository, {})

    assert await worker.process_one() is True
    assert repository.jobs[0].status == "failed"


def test_worker_retries_a_failed_job_before_marking_it_failed() -> None:
    asyncio.run(_worker_retries_a_failed_job_before_marking_it_failed())


async def _worker_retries_a_failed_job_before_marking_it_failed() -> None:
    repository = InMemoryJobRepository()
    await repository.enqueue(
        JobCreate(
            job_type="survey_response",
            idempotency_key="submission-3",
            payload={"wave": 1},
        )
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
        JobCreate(
            job_type="survey_response",
            idempotency_key="submission-4",
            payload={"wave": 1},
        )
    )
    await repository.claim_next()
    worker = QueueWorker(repository, {})

    assert await worker.recover() == 1
    assert repository.jobs[0].status == "queued"

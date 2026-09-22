import asyncio
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.schemas.chat import ChatSubmissionRecord, ParsedTranscript
from app.services.screenshot_transcripts import (
    ScreenshotImage,
    capture_time_from_filename,
    normalize_screenshot_extraction,
    order_screenshot_images,
)
from app.services.transcript_runs import build_transcript_archive, process_transcript_parse


def image(filename: str) -> ScreenshotImage:
    return ScreenshotImage(filename, "image/jpeg", b"jpeg")


def test_capture_time_is_parsed_as_kst_capture_metadata() -> None:
    assert capture_time_from_filename("Screenshot_20260921_205407.jpg") == "2026-09-21T20:54:07+09:00"
    assert capture_time_from_filename("screen.jpg") is None


def test_orders_screenshots_by_filename_capture_time() -> None:
    images = [
        image("Screenshot_20260921_205431.jpg"),
        image("Screenshot_20260921_205407.jpg"),
    ]

    assert [item.filename for item in order_screenshot_images(images)] == [
        "Screenshot_20260921_205407.jpg",
        "Screenshot_20260921_205431.jpg",
    ]


def test_normalizes_validated_turns_and_deduplicates_exact_overlap() -> None:
    images = [
        image("Screenshot_20260921_205407.jpg"),
        image("Screenshot_20260921_205413.jpg"),
    ]
    extracted = {
        "sessionTitle": "엔믹스 정보",
        "turns": [
            {
                "role": "user",
                "content": "엔믹스 정보",
                "timestamp": "2026-09-21T20:51:00+09:00",
                "sourceImageIndexes": [1],
                "confidence": 0.99,
            },
            {
                "role": "assistant",
                "content": "엔믹스에 대해 알려드리겠습니다.",
                "timestamp": "2026-09-21T20:51:00+09:00",
                "sourceImageIndexes": [1],
                "confidence": 0.95,
            },
            {
                "role": "assistant",
                "content": "엔믹스에 대해 알려드리겠습니다.",
                "timestamp": "2026-09-21T20:51:00+09:00",
                "sourceImageIndexes": [2],
                "confidence": 0.95,
            },
            {"role": "system", "content": "상태바", "sourceImageIndexes": [1]},
            {"role": "user", "content": "", "sourceImageIndexes": [3]},
        ],
    }

    normalized, warnings = normalize_screenshot_extraction(extracted, images, "participant-1", "other")

    assert warnings == []
    assert normalized["sourceType"] == "screenshot_images"
    assert normalized["summary"]["totalTurns"] == 2
    session = normalized["sessions"][0]
    assert session["sessionId"].startswith("screenshot_")
    assert session["sessionMetadata"]["captureStartTime"] == "2026-09-21T20:54:07+09:00"
    assert session["sessionMetadata"]["captureEndTime"] == "2026-09-21T20:54:13+09:00"
    assert session["sessionMetadata"]["startTime"] == "2026-09-21T20:51:00+09:00"
    assert session["turns"][1]["turnMetadata"]["sourceImageIndexes"] == [1, 2]


def test_low_confidence_turn_requires_review() -> None:
    normalized, warnings = normalize_screenshot_extraction(
        {
            "turns": [
                {
                    "role": "user",
                    "content": "설윤 사진",
                    "sourceImageIndexes": [1],
                    "confidence": 0.65,
                }
            ]
        },
        [image("screen.jpg")],
        "participant-1",
        "other",
    )

    assert normalized["sessions"][0]["turns"][0]["timestamp"] is None
    assert warnings == ["1번 발화는 OCR 검토가 필요합니다."]


class FakeExtractor:
    def __init__(self) -> None:
        self.filenames: list[str] = []

    async def extract(self, images: list[ScreenshotImage]) -> dict[str, Any]:
        self.filenames = [image.filename for image in images]
        return {
            "sessionTitle": "엔믹스 정보",
            "turns": [
                {"role": "user", "content": "엔믹스 정보", "sourceImageIndexes": [1], "confidence": 0.99},
                {
                    "role": "assistant",
                    "content": "엔믹스에 대해 알려드리겠습니다.",
                    "sourceImageIndexes": [1, 2],
                    "confidence": 0.98,
                },
            ],
        }


class FakeSubmissions:
    def __init__(self, submission: ChatSubmissionRecord) -> None:
        self.submission = submission

    async def list_submissions(
        self, submission_point: str | None = None, status: str = "active"
    ) -> list[ChatSubmissionRecord]:
        return [self.submission]

    async def update_transcript(self, submission_id: str, transcript: ParsedTranscript) -> bool:
        self.submission = self.submission.model_copy(update={"transcript": transcript})
        return True


class FakeUploads:
    async def read_bytes(self, file_id: str) -> tuple[str, bytes, dict[str, Any]]:
        filename = {
            "later": "Screenshot_20260921_205413.jpg",
            "earlier": "Screenshot_20260921_205407.jpg",
        }[file_id]
        return filename, b"jpeg", {"content_type": "image/jpeg"}


class FakeRuns:
    def __init__(self) -> None:
        self.completed: dict[str, Any] | None = None

    async def complete(
        self,
        run_id: str,
        parser_name: str,
        parser_version: str,
        normalized_json: dict[str, Any],
        warnings: list[str],
        status: str = "completed",
    ) -> None:
        self.completed = {
            "parser_name": parser_name,
            "parser_version": parser_version,
            "normalized_json": normalized_json,
            "warnings": warnings,
            "status": status,
        }

    async def fail(self, run_id: str, error: str, warnings: list[str]) -> None:
        raise AssertionError(error)

    async def latest(self, submission_id: str) -> None:
        return None

    async def create(self, submission_id: str, parser_name: str, parser_version: str) -> SimpleNamespace:
        return SimpleNamespace(run_id="run-1")

    async def get(self, run_id: str) -> SimpleNamespace | None:
        if self.completed is None:
            return None
        return SimpleNamespace(
            status=self.completed["status"],
            normalized_json=self.completed["normalized_json"],
            warnings=self.completed["warnings"],
            error=None,
        )


def test_processes_all_image_attachments_through_extractor() -> None:
    submission = ChatSubmissionRecord(
        submissionId="submission-1",
        participantId="participant-1",
        submissionPoint="afterRound1",
        sourceType="image",
        tool="other",
        rawInput="screenshots",
        transcript=ParsedTranscript(
            status="placeholder", parserVersion="attachment-v1", messages=[], plainText="", warnings=[]
        ),
        submittedAt=datetime.now(UTC),
        attachments=[
            {"fileId": "later", "filename": "later.jpg", "contentType": "image/jpeg", "size": 4},
            {"fileId": "earlier", "filename": "earlier.jpg", "contentType": "image/jpeg", "size": 4},
        ],
    )
    submissions = FakeSubmissions(submission)
    runs = FakeRuns()
    extractor = FakeExtractor()

    asyncio.run(
        process_transcript_parse(
            {"submissionId": "submission-1", "runId": "run-1"},
            submissions,
            FakeUploads(),
            runs,
            extractor,
        )
    )

    assert extractor.filenames == [
        "Screenshot_20260921_205407.jpg",
        "Screenshot_20260921_205413.jpg",
    ]
    assert submissions.submission.transcript.status == "parsed"
    assert runs.completed is not None
    assert runs.completed["parser_version"] == "screenshot-vlm-v1"
    assert runs.completed["normalized_json"]["summary"]["totalTurns"] == 2


def test_transcript_archive_uses_screenshot_extractor() -> None:
    submission = ChatSubmissionRecord(
        submissionId="submission-1",
        participantId="participant-1",
        submissionPoint="afterRound1",
        sourceType="image",
        tool="other",
        rawInput="screenshots",
        transcript=ParsedTranscript(
            status="placeholder", parserVersion="attachment-v1", messages=[], plainText="", warnings=[]
        ),
        submittedAt=datetime.now(UTC),
        attachments=[
            {"fileId": "earlier", "filename": "earlier.jpg", "contentType": "image/jpeg", "size": 4}
        ],
    )
    submissions = FakeSubmissions(submission)
    runs = FakeRuns()

    with tempfile.TemporaryDirectory() as directory:
        archive_path = Path(directory) / "transcripts.zip"
        completed, failed = asyncio.run(
            build_transcript_archive(
                [submission],
                submissions,
                FakeUploads(),
                runs,
                archive_path,
                screenshot_extractor=FakeExtractor(),
            )
        )
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()

    assert (completed, failed) == (1, 0)
    assert names == ["transcript-submission-1.csv"]

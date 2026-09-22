import asyncio
import csv
import io
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
from app.services.transcript_runs import build_transcript_archive, normalized_to_csv, process_transcript_parse


def image(filename: str, data: bytes = b"jpeg") -> ScreenshotImage:
    return ScreenshotImage(filename, "image/jpeg", data)


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
    assert warnings == [
        "1번 발화는 OCR 검토가 필요합니다.",
        "발화가 1개만 추출되어 대화 왕복 및 발화 경계 검토가 필요합니다.",
    ]
    assert normalized["reviewWarnings"] == warnings
    assert "대화 왕복 및 발화 경계 검토" in normalized_to_csv(normalized)


def test_normalizes_visible_korean_time_using_source_image_date() -> None:
    normalized, warnings = normalize_screenshot_extraction(
        {
            "turns": [
                {
                    "role": "user",
                    "content": "질문",
                    "timestamp": "오후 8:51",
                    "sourceImageIndexes": [1],
                    "confidence": 1.0,
                }
            ]
        },
        [image("Screenshot_20260921_205407.jpg")],
        "participant-1",
        "other",
    )

    turn = normalized["sessions"][0]["turns"][0]
    assert turn["timestamp"] == "2026-09-21T20:51:00+09:00"
    assert normalized["sessions"][0]["sessionMetadata"]["startTime"] == turn["timestamp"]
    assert warnings == ["발화가 1개만 추출되어 대화 왕복 및 발화 경계 검토가 필요합니다."]


def test_fingerprint_uses_image_content_and_warns_about_missing_evidence() -> None:
    extracted = {"turns": [{"role": "user", "content": "질문"}]}

    first, first_warnings = normalize_screenshot_extraction(
        extracted, [image("same.jpg", b"first")], "participant-1", "other"
    )
    second, _ = normalize_screenshot_extraction(extracted, [image("same.jpg", b"second")], "participant-1", "other")

    assert first["sessions"][0]["sessionId"] != second["sessions"][0]["sessionId"]
    assert first_warnings == [
        "1번 발화에 출처 이미지 정보가 없습니다.",
        "1번 발화에 OCR 신뢰도 정보가 없습니다.",
        "발화가 1개만 추출되어 대화 왕복 및 발화 경계 검토가 필요합니다.",
    ]


def test_warns_when_multiple_turns_have_only_one_role() -> None:
    _, warnings = normalize_screenshot_extraction(
        {
            "turns": [
                {"role": "assistant", "content": "첫 장면", "sourceImageIndexes": [1], "confidence": 1},
                {"role": "assistant", "content": "둘째 장면", "sourceImageIndexes": [1], "confidence": 1},
            ]
        },
        [image("screen.png")],
        "participant-1",
        "zeta",
    )

    assert warnings == ["한 역할의 발화만 추출되어 역할 구분 검토가 필요합니다."]


def test_preserves_visible_speaker_label_without_prefixing_content() -> None:
    normalized, _ = normalize_screenshot_extraction(
        {
            "turns": [
                {
                    "role": "assistant",
                    "speakerLabel": "류",
                    "content": "같은 대사",
                    "sourceImageIndexes": [1],
                    "confidence": 1,
                },
                {
                    "role": "assistant",
                    "speakerLabel": "엘나",
                    "content": "같은 대사",
                    "sourceImageIndexes": [1],
                    "confidence": 1,
                },
            ]
        },
        [image("zeta.png")],
        "participant-1",
        "zeta",
    )

    turns = normalized["sessions"][0]["turns"]
    assert len(turns) == 2
    assert [turn["content"] for turn in turns] == ["같은 대사", "같은 대사"]
    assert [turn["turnMetadata"]["speakerLabel"] for turn in turns] == ["류", "엘나"]
    csv_rows = list(csv.DictReader(io.StringIO(normalized_to_csv(normalized))))
    assert [row["speakerLabel"] for row in csv_rows] == ["류", "엘나"]


class FakeExtractor:
    def __init__(self) -> None:
        self.filenames: list[str] = []
        self.platform: str | None = None

    async def extract(self, images: list[ScreenshotImage], *, platform: str | None = None) -> dict[str, Any]:
        self.filenames = [image.filename for image in images]
        self.platform = platform
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
    assert extractor.platform == "other"
    assert submissions.submission.transcript.status == "parsed"
    assert runs.completed is not None
    assert runs.completed["parser_version"] == "screenshot-vlm-v3"
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
        attachments=[{"fileId": "earlier", "filename": "earlier.jpg", "contentType": "image/jpeg", "size": 4}],
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

from datetime import UTC, datetime

from app.schemas.chat import (
    ChatSubmissionRecord,
    ChatSubmissionReview,
    ParsedTranscript,
    TranscriptMessage,
)
from app.services.chats import chat_submissions_to_csv


def test_chat_export_includes_raw_input_and_normalized_transcript() -> None:
    csv_text = chat_submissions_to_csv(
        [
            ChatSubmissionRecord(
                participantId="participant-1",
                submissionPoint="afterRound1",
                sourceType="text",
                rawInput="사용자: 안녕하세요",
                transcript=ParsedTranscript(
                    status="parsed",
                    parserVersion="dummy-v1",
                    messages=[TranscriptMessage(speaker="user", text="안녕하세요")],
                    plainText="user: 안녕하세요",
                    warnings=[],
                ),
                submittedAt=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
                review=ChatSubmissionReview(
                    status="other",
                    note="대화 내용 부족",
                    updatedAt=datetime(2026, 9, 22, 12, 0, tzinfo=UTC),
                    updatedBy="researcher-1",
                ),
            )
        ],
        {"participant-1": ("홍길동", "초등", 4)},
    )

    assert csv_text.splitlines()[0].startswith("participantId,name,schoolLevel,grade")
    assert "홍길동,초등,4" in csv_text
    assert "user: 안녕하세요" in csv_text
    assert "사용자: 안녕하세요" in csv_text
    assert "reviewStatus,reviewNote,reviewedAt,reviewedBy" in csv_text
    assert "other,대화 내용 부족,2026-09-22T12:00:00+00:00,researcher-1" in csv_text

from datetime import UTC, datetime

from app.schemas.chat import ChatSubmissionRecord, ParsedTranscript, TranscriptMessage
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
            )
        ],
        {"participant-1": ("홍길동", "초등", 4)},
    )

    assert csv_text.splitlines()[0].startswith("participantId,name,schoolLevel,grade")
    assert "홍길동,초등,4" in csv_text
    assert "user: 안녕하세요" in csv_text
    assert "사용자: 안녕하세요" in csv_text

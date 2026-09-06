from app.services.transcripts import parse_transcript


def test_text_parser_normalizes_recognized_speakers() -> None:
    transcript = parse_transcript("text", "사용자: 안녕하세요\nAI: 무엇을 도와드릴까요?")

    assert transcript.status == "parsed"
    assert [message.speaker for message in transcript.messages] == ["user", "assistant"]
    assert transcript.plain_text == "user: 안녕하세요\nassistant: 무엇을 도와드릴까요?"


def test_link_parser_preserves_placeholder_state_until_service_adapter_exists() -> None:
    transcript = parse_transcript("link", "https://example.com/shared-chat")

    assert transcript.status == "placeholder"
    assert transcript.messages == []
    assert transcript.warnings

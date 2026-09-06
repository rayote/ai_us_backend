from __future__ import annotations

from urllib.parse import urlparse

from app.schemas.chat import ChatSourceType, ParsedTranscript, TranscriptMessage

PARSER_VERSION = "dummy-v1"

_SPEAKER_PREFIXES = {
    "user:": "user",
    "사용자:": "user",
    "human:": "user",
    "assistant:": "assistant",
    "ai:": "assistant",
    "챗gpt:": "assistant",
}


def parse_transcript(source_type: ChatSourceType, raw_input: str) -> ParsedTranscript:
    if source_type == "link":
        return _parse_link(raw_input)
    return _parse_text(raw_input)


def _parse_link(raw_input: str) -> ParsedTranscript:
    url = urlparse(raw_input.strip())
    if url.scheme not in {"http", "https"} or not url.netloc:
        return ParsedTranscript(
            status="warning",
            parserVersion=PARSER_VERSION,
            messages=[],
            plainText="",
            warnings=["유효한 대화 공유 링크가 아닙니다."],
        )
    return ParsedTranscript(
        status="placeholder",
        parserVersion=PARSER_VERSION,
        messages=[],
        plainText="",
        warnings=["링크 파서는 아직 서비스별 adapter가 없어 대화문을 추출하지 않았습니다."],
    )


def _parse_text(raw_input: str) -> ParsedTranscript:
    messages: list[TranscriptMessage] = []
    current_speaker = "unknown"
    current_lines: list[str] = []
    recognized_speaker = False
    for line in raw_input.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        speaker, text = _split_speaker(stripped)
        if speaker is not None:
            if current_lines:
                messages.append(TranscriptMessage(speaker=current_speaker, text="\n".join(current_lines)))
            current_speaker = speaker
            current_lines = [text] if text else []
            recognized_speaker = True
        elif stripped:
            current_lines.append(stripped)
    if current_lines:
        messages.append(TranscriptMessage(speaker=current_speaker, text="\n".join(current_lines)))
    if not messages:
        return ParsedTranscript(
            status="warning",
            parserVersion=PARSER_VERSION,
            messages=[],
            plainText="",
            warnings=["대화문 본문이 비어 있습니다."],
        )
    warnings = [] if recognized_speaker else ["화자 표식을 찾지 못해 본문 전체를 하나의 대화로 보관했습니다."]
    return ParsedTranscript(
        status="parsed" if recognized_speaker else "warning",
        parserVersion=PARSER_VERSION,
        messages=messages,
        plainText="\n".join(f"{message.speaker}: {message.text}" for message in messages),
        warnings=warnings,
    )


def _split_speaker(line: str) -> tuple[str | None, str]:
    lowered = line.lower()
    for prefix, speaker in _SPEAKER_PREFIXES.items():
        if lowered.startswith(prefix):
            return speaker, line[len(prefix) :].strip()
    return None, line

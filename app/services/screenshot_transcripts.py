from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
SCHEMA_VERSION = "transcript-v1"
_FILENAME_TIMESTAMP = re.compile(r"(?<!\d)(20\d{6})[_-]?(\d{6})(?!\d)")
_KOREAN_TIME = re.compile(r"^(오전|오후)\s*(\d{1,2}):(\d{2})$")


class ScreenshotExtractionError(ValueError):
    pass


@dataclass(frozen=True)
class ScreenshotImage:
    filename: str
    content_type: str
    data: bytes


class ScreenshotTranscriptExtractor(Protocol):
    async def extract(self, images: list[ScreenshotImage], *, platform: str | None = None) -> dict[str, Any]: ...


def order_screenshot_images(images: list[ScreenshotImage]) -> list[ScreenshotImage]:
    return sorted(
        images,
        key=lambda image: (
            capture_time_from_filename(image.filename) is None,
            capture_time_from_filename(image.filename) or image.filename.lower(),
        ),
    )


def capture_time_from_filename(filename: str) -> str | None:
    match = _FILENAME_TIMESTAMP.search(Path(filename).stem)
    if match is None:
        return None
    try:
        return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S").replace(tzinfo=KST).isoformat()
    except ValueError:
        return None


def _normalize_message_timestamp(
    value: Any, source_image_indexes: list[int], images: list[ScreenshotImage]
) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    timestamp = value.strip()
    match = _KOREAN_TIME.fullmatch(timestamp)
    if match is None or not source_image_indexes:
        return timestamp

    capture_time = capture_time_from_filename(images[source_image_indexes[0] - 1].filename)
    if capture_time is None:
        return timestamp
    period, hour_text, minute_text = match.groups()
    hour = int(hour_text)
    minute = int(minute_text)
    if not 1 <= hour <= 12 or not 0 <= minute <= 59:
        return timestamp
    if period == "오전":
        hour = 0 if hour == 12 else hour
    else:
        hour = hour if hour == 12 else hour + 12
    return datetime.fromisoformat(capture_time).replace(hour=hour, minute=minute, second=0).isoformat()


def _image_fingerprint(images: list[ScreenshotImage]) -> str:
    digest = hashlib.sha256()
    for image in images:
        digest.update(image.filename.encode())
        digest.update(b"\0")
        digest.update(image.data)
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def normalize_screenshot_extraction(
    extracted: dict[str, Any],
    images: list[ScreenshotImage],
    participant_id: str,
    platform: str,
) -> tuple[dict[str, Any], list[str]]:
    if not images:
        raise ScreenshotExtractionError("파싱할 스크린샷이 없습니다.")

    turns: list[dict[str, Any]] = []
    warnings: list[str] = []
    for raw_turn in extracted.get("turns", []):
        if not isinstance(raw_turn, dict):
            continue
        role = raw_turn.get("role")
        content = str(raw_turn.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue

        source_images = sorted(
            {
                index
                for index in raw_turn.get("sourceImageIndexes", [])
                if isinstance(index, int) and 1 <= index <= len(images)
            }
        )
        turn_number = len(turns) + 1
        if not source_images:
            warnings.append(f"{turn_number}번 발화에 출처 이미지 정보가 없습니다.")
        turn_metadata: dict[str, Any] = {"sourceImageIndexes": source_images}
        speaker_label = str(raw_turn.get("speakerLabel") or "").strip() or None
        if speaker_label is not None:
            turn_metadata["speakerLabel"] = speaker_label
        confidence = raw_turn.get("confidence")
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
            turn_metadata["confidence"] = max(0.0, min(float(confidence), 1.0))
            if confidence < 0.8:
                warnings.append(f"{turn_number}번 발화는 OCR 검토가 필요합니다.")
        else:
            warnings.append(f"{turn_number}번 발화에 OCR 신뢰도 정보가 없습니다.")

        turn = {
            "turnId": turn_number,
            "role": role,
            "content": content,
            "timestamp": _normalize_message_timestamp(raw_turn.get("timestamp"), source_images, images),
            "turnMetadata": turn_metadata,
        }
        if turns and all(
            (
                turns[-1]["role"] == role,
                turns[-1]["content"] == content,
                turns[-1]["turnMetadata"].get("speakerLabel") == speaker_label,
            )
        ):
            turns[-1]["turnMetadata"]["sourceImageIndexes"] = sorted(
                set(turns[-1]["turnMetadata"]["sourceImageIndexes"]) | set(source_images)
            )
            continue
        turns.append(turn)

    if not turns:
        raise ScreenshotExtractionError("스크린샷에서 유효한 대화 발화를 찾지 못했습니다.")
    if len(turns) == 1:
        warnings.append("발화가 1개만 추출되어 대화 왕복 및 발화 경계 검토가 필요합니다.")
    elif len({turn["role"] for turn in turns}) == 1:
        warnings.append("한 역할의 발화만 추출되어 역할 구분 검토가 필요합니다.")

    capture_times = [capture_time_from_filename(image.filename) for image in images]
    known_capture_times = [value for value in capture_times if value is not None]
    fingerprint = _image_fingerprint(images)
    session_metadata = {
        "startTime": next((turn["timestamp"] for turn in turns if turn["timestamp"]), None),
        "endTime": next((turn["timestamp"] for turn in reversed(turns) if turn["timestamp"]), None),
        "durationSeconds": None,
        "totalTurns": len(turns),
        "userTurns": sum(turn["role"] == "user" for turn in turns),
        "assistantTurns": sum(turn["role"] == "assistant" for turn in turns),
        "captureStartTime": min(known_capture_times) if known_capture_times else None,
        "captureEndTime": max(known_capture_times) if known_capture_times else None,
        "sourceImages": [image.filename for image in images],
    }
    extraction_metadata = extracted.get("extractionMetadata")
    if isinstance(extraction_metadata, dict):
        session_metadata["extraction"] = extraction_metadata
    warnings = list(dict.fromkeys(warnings))
    normalized = {
        "participantId": participant_id,
        "platform": platform or "other",
        "sourceType": "screenshot_images",
        "schemaVersion": SCHEMA_VERSION,
        "parsedAt": datetime.now(KST).isoformat(),
        "reviewWarnings": warnings,
        "summary": {
            "totalSessions": 1,
            "totalTurns": len(turns),
            "firstActivityTime": session_metadata["startTime"],
            "lastActivityTime": session_metadata["endTime"],
        },
        "sessions": [
            {
                "sessionId": f"screenshot_{fingerprint}",
                "originalSessionId": images[0].filename,
                "sessionTitle": str(extracted.get("sessionTitle") or "스크린샷 대화").strip(),
                "sessionMetadata": session_metadata,
                "turns": turns,
            }
        ],
    }
    return normalized, warnings

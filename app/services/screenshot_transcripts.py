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


class ScreenshotExtractionError(ValueError):
    pass


@dataclass(frozen=True)
class ScreenshotImage:
    filename: str
    content_type: str
    data: bytes


class ScreenshotTranscriptExtractor(Protocol):
    async def extract(self, images: list[ScreenshotImage]) -> dict[str, Any]:
        ...


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
        turn_metadata: dict[str, Any] = {"sourceImageIndexes": source_images}
        confidence = raw_turn.get("confidence")
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
            turn_metadata["confidence"] = max(0.0, min(float(confidence), 1.0))
            if confidence < 0.8:
                warnings.append(f"{len(turns) + 1}번 발화는 OCR 검토가 필요합니다.")

        turn = {
            "turnId": len(turns) + 1,
            "role": role,
            "content": content,
            "timestamp": raw_turn.get("timestamp") or None,
            "turnMetadata": turn_metadata,
        }
        if turns and turns[-1]["role"] == role and turns[-1]["content"] == content:
            turns[-1]["turnMetadata"]["sourceImageIndexes"] = sorted(
                set(turns[-1]["turnMetadata"]["sourceImageIndexes"]) | set(source_images)
            )
            continue
        turns.append(turn)

    if not turns:
        raise ScreenshotExtractionError("스크린샷에서 유효한 대화 발화를 찾지 못했습니다.")

    capture_times = [capture_time_from_filename(image.filename) for image in images]
    known_capture_times = [value for value in capture_times if value is not None]
    fingerprint = hashlib.sha256("\n".join(image.filename for image in images).encode()).hexdigest()[:16]
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
    normalized = {
        "participantId": participant_id,
        "platform": platform or "other",
        "sourceType": "screenshot_images",
        "schemaVersion": SCHEMA_VERSION,
        "parsedAt": datetime.now(KST).isoformat(),
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
    return normalized, list(dict.fromkeys(warnings))

from __future__ import annotations

import json
from typing import Any

from app.services.screenshot_transcripts import ScreenshotExtractionError, ScreenshotImage

DEFAULT_MODEL = "gemini-3.8-flash"
PROMPT_VERSION = "screenshot-transcript-v5"
MAX_INLINE_REQUEST_BYTES = 20 * 1024 * 1024
SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}

EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sessionTitle": {"type": "string"},
        "turns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "enum": ["user", "assistant"]},
                    "speakerLabel": {"type": ["string", "null"]},
                    "content": {"type": "string"},
                    "timestamp": {"type": ["string", "null"]},
                    "sourceImageIndexes": {"type": "array", "items": {"type": "integer"}},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": [
                    "role",
                    "speakerLabel",
                    "content",
                    "timestamp",
                    "sourceImageIndexes",
                    "confidence",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["sessionTitle", "turns"],
    "additionalProperties": False,
}

EXTRACTION_PROMPT = """Transcribe the ordered screenshots as one continuous chatbot conversation.

Rules:
- Merge text fragments and repeated overlap across adjacent screenshots into one complete turn.
- Keep visually separate message or narration blocks as separate turns, even when they have the same role.
- Right-aligned user bubbles are role user. Left-aligned or full-width chatbot text with an avatar/name is role assistant.
- speakerLabel is the visible sender or character name attached to that block, without punctuation. Use null when no name is visibly attached. Never add speakerLabel to content.
- Ignore status bars, page titles, date separators, navigation, message composers, buttons, and other interface chrome.
- Preserve visible message text exactly, including Korean spelling, punctuation, and paragraph breaks.
- Never invent text hidden above, below, or behind a clipped screenshot edge.
- Use a timestamp only when it is visibly attached to that message. Otherwise use null.
- Screenshot filename times are capture metadata, not message timestamps.
- sourceImageIndexes are the 1-based image numbers where any part of the turn is visible.
- confidence expresses transcription confidence from 0 to 1.
"""

ZETA_EXTRACTION_PROMPT = """Zeta-specific role rules override the generic alignment rules:
- A right-aligned bubble is a user turn. Bubble color is only a secondary clue because themes may change it.
- A left-aligned bubble with a character avatar or character name is an assistant turn.
- Text outside user bubbles is assistant output, including background narration, instructions, scene descriptions, character dialogue, and NPC dialogue.
- Do not invent a user turn when no unambiguous user-message bubble is visible.
- Preserve any visible player or character name in speakerLabel, but never prepend it to content.
- Keep each visually separate block as a separate turn. Do not merge adjacent assistant blocks.
"""


class GeminiScreenshotTranscriptExtractor:
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        *,
        client: Any | None = None,
        types_module: Any | None = None,
    ) -> None:
        if not api_key and client is None:
            raise ValueError("Gemini API key가 필요합니다.")
        if client is None or types_module is None:
            try:
                from google import genai
                from google.genai import types
            except ImportError as error:
                raise RuntimeError("Gemini 이미지 파싱에는 google-genai 패키지가 필요합니다.") from error
            client = client or genai.Client(api_key=api_key)
            types_module = types_module or types
        self._client = client
        self._types = types_module
        self._model = model

    async def extract(self, images: list[ScreenshotImage], *, platform: str | None = None) -> dict[str, Any]:
        self._validate_images(images)
        extracted_batches = []
        for batch_number, batch in enumerate(self._image_batches(images), start=1):
            extracted_batches.append(await self._extract_batch(batch, batch_number, platform))

        return {
            "sessionTitle": next(
                (
                    str(extracted.get("sessionTitle") or "").strip()
                    for extracted in extracted_batches
                    if str(extracted.get("sessionTitle") or "").strip()
                ),
                "스크린샷 대화",
            ),
            "turns": [turn for extracted in extracted_batches for turn in extracted.get("turns", [])],
            "extractionMetadata": {
                "provider": "google-gemini",
                "model": self._model,
                "promptVersion": PROMPT_VERSION,
                "batchCount": len(extracted_batches),
                "platformHint": platform or None,
            },
        }

    async def _extract_batch(
        self,
        indexed_images: list[tuple[int, ScreenshotImage]],
        batch_number: int,
        platform: str | None,
    ) -> dict[str, Any]:
        prompt = EXTRACTION_PROMPT
        if (platform or "").lower() == "zeta":
            prompt += "\n" + ZETA_EXTRACTION_PROMPT
        parts = [self._types.Part.from_text(text=prompt)]
        for index, image in indexed_images:
            parts.append(self._types.Part.from_text(text=f"Image {index}: {image.filename}"))
            parts.append(self._types.Part.from_bytes(data=image.data, mime_type=image.content_type))

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=[self._types.UserContent(parts=parts)],
                config=self._types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=EXTRACTION_SCHEMA,
                    temperature=0,
                ),
            )
        except Exception as error:
            raise ScreenshotExtractionError(
                f"Gemini 스크린샷 {batch_number}번 묶음 추출에 실패했습니다: {error}"
            ) from error

        parsed = response.parsed
        if parsed is None:
            try:
                parsed = json.loads(response.text or "")
            except (TypeError, json.JSONDecodeError) as error:
                raise ScreenshotExtractionError("Gemini가 유효한 JSON 응답을 반환하지 않았습니다.") from error
        if not isinstance(parsed, dict):
            raise ScreenshotExtractionError("Gemini 추출 결과는 JSON 객체여야 합니다.")
        return parsed

    @staticmethod
    def _image_batches(images: list[ScreenshotImage]) -> list[list[tuple[int, ScreenshotImage]]]:
        batches: list[list[tuple[int, ScreenshotImage]]] = []
        current: list[tuple[int, ScreenshotImage]] = []
        current_bytes = 0
        for index, image in enumerate(images, start=1):
            image_bytes = len(image.data)
            if current and current_bytes + image_bytes >= MAX_INLINE_REQUEST_BYTES:
                batches.append(current)
                overlap = current[-1]
                if len(overlap[1].data) + image_bytes < MAX_INLINE_REQUEST_BYTES:
                    current = [overlap]
                    current_bytes = len(overlap[1].data)
                else:
                    current = []
                    current_bytes = 0
            current.append((index, image))
            current_bytes += image_bytes
        if current:
            batches.append(current)
        return batches

    @staticmethod
    def _validate_images(images: list[ScreenshotImage]) -> None:
        if not images:
            raise ScreenshotExtractionError("Gemini로 전송할 스크린샷이 없습니다.")
        unsupported = [image.filename for image in images if image.content_type not in SUPPORTED_IMAGE_TYPES]
        if unsupported:
            raise ScreenshotExtractionError(f"지원하지 않는 이미지 형식입니다: {', '.join(unsupported)}")
        oversized = [image.filename for image in images if len(image.data) >= MAX_INLINE_REQUEST_BYTES]
        if oversized:
            raise ScreenshotExtractionError(
                f"이미지 한 장이 Gemini inline 요청 크기 제한(20MB)을 초과했습니다: {', '.join(oversized)}"
            )

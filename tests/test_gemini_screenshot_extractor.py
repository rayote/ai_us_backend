import asyncio
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from app.services.gemini_screenshot_extractor import (
    EXTRACTION_SCHEMA,
    MAX_INLINE_REQUEST_BYTES,
    GeminiScreenshotTranscriptExtractor,
)
from app.services.screenshot_transcripts import (
    ScreenshotExtractionError,
    ScreenshotImage,
    normalize_screenshot_extraction,
)


class FakePart:
    @staticmethod
    def from_text(*, text: str) -> tuple[str, str]:
        return "text", text

    @staticmethod
    def from_bytes(*, data: bytes, mime_type: str) -> tuple[str, bytes, str]:
        return "image", data, mime_type


class FakeTypes:
    Part = FakePart

    @staticmethod
    def UserContent(*, parts: list[Any]) -> SimpleNamespace:
        return SimpleNamespace(parts=parts)

    @staticmethod
    def GenerateContentConfig(**values: Any) -> SimpleNamespace:
        return SimpleNamespace(**values)


class FakeModels:
    def __init__(self, parsed: Any, additional: list[Any] | None = None) -> None:
        self.responses = [parsed, *(additional or [])]
        self.request: dict[str, Any] | None = None
        self.requests: list[dict[str, Any]] = []

    async def generate_content(self, **request: Any) -> SimpleNamespace:
        self.request = request
        self.requests.append(request)
        return SimpleNamespace(parsed=self.responses[len(self.requests) - 1], text=None)


def image(filename: str, data: bytes = b"jpeg", content_type: str = "image/jpeg") -> ScreenshotImage:
    return ScreenshotImage(filename, content_type, data)


def test_sends_ordered_images_in_one_structured_request() -> None:
    expected = {"sessionTitle": "엔믹스 정보", "turns": []}
    models = FakeModels(expected)
    client = SimpleNamespace(aio=SimpleNamespace(models=models))
    extractor = GeminiScreenshotTranscriptExtractor("", client=client, types_module=FakeTypes)

    result = asyncio.run(extractor.extract([image("first.jpg"), image("second.jpg")]))

    assert result == {
        **expected,
        "extractionMetadata": {
            "provider": "google-gemini",
            "model": "gemini-3.8-flash",
            "promptVersion": "screenshot-transcript-v6",
            "batchCount": 1,
            "platformHint": None,
        },
    }
    assert models.request is not None
    assert models.request["model"] == "gemini-3.8-flash"
    parts = models.request["contents"][0].parts
    assert "visually separate" in parts[0][1]
    assert "Zeta-specific" not in parts[0][1]
    assert parts[1] == ("text", "Image 1: first.jpg")
    assert parts[2] == ("image", b"jpeg", "image/jpeg")
    assert parts[3] == ("text", "Image 2: second.jpg")
    assert parts[4] == ("image", b"jpeg", "image/jpeg")
    assert models.request["config"].response_json_schema is EXTRACTION_SCHEMA


def test_adds_zeta_specific_role_rules_only_for_zeta() -> None:
    models = FakeModels({"sessionTitle": "Zeta", "turns": []})
    client = SimpleNamespace(aio=SimpleNamespace(models=models))
    extractor = GeminiScreenshotTranscriptExtractor("", client=client, types_module=FakeTypes)

    result = asyncio.run(extractor.extract([image("zeta.png")], platform="zeta"))

    assert models.request is not None
    prompt = models.request["contents"][0].parts[0][1]
    assert "A right-aligned bubble is a user turn" in prompt
    assert "Bubble color is only a secondary clue" in prompt
    assert "character avatar or character name" in prompt
    assert "Do not invent a user turn" in prompt
    assert "NPC dialogue" in prompt
    assert "speakerLabel" in prompt
    turn_schema = EXTRACTION_SCHEMA["properties"]["turns"]["items"]
    assert turn_schema["properties"]["speakerLabel"] == {"type": ["string", "null"]}
    assert "speakerLabel" in turn_schema["required"]
    assert result["extractionMetadata"]["platformHint"] == "zeta"


def test_long_screenshots_are_cropped_without_losing_rows_or_global_source_indexes() -> None:
    with Image.new("RGB", (300, 5000)) as source:
        for row in range(source.height):
            source.paste((row % 256, row // 256, 0), (0, row, source.width, row + 1))
        output = BytesIO()
        source.save(output, format="PNG")
        original = image("long.png", output.getvalue(), "image/png")
        batches = GeminiScreenshotTranscriptExtractor._image_batches([image("first.jpg"), original])
        regions = [region for batch in batches for index, region in batch if index == 2]
        assert len(regions) == 3
        assert all(region.content_type == "image/png" for region in regions)
        for region, top in zip(regions, [0, 1888, 3776]):
            with Image.open(BytesIO(region.data)) as crop:
                assert crop.width == source.width
                assert crop.height == min(2048, source.height - top)
                assert crop.tobytes() == source.crop((0, top, source.width, top + crop.height)).tobytes()
            assert f"rows {top}:" in region.filename
        assert regions[-1].filename.endswith("5000 of 5000]")


def test_regular_screenshots_keep_original_bytes() -> None:
    output = BytesIO()
    with Image.new("RGB", (600, 1200), "white") as source:
        source.save(output, format="PNG")
    original = image("normal.png", output.getvalue(), "image/png")
    assert GeminiScreenshotTranscriptExtractor._image_regions(original) == [original]


@pytest.mark.parametrize("reason", ["SAFETY", "MAX_TOKENS"])
def test_empty_provider_response_preserves_finish_reason(reason: str) -> None:
    response = SimpleNamespace(parsed=None, text=None, candidates=[SimpleNamespace(finish_reason=reason)])
    models = SimpleNamespace(generate_content=AsyncMock(return_value=response))
    extractor = GeminiScreenshotTranscriptExtractor(
        "", client=SimpleNamespace(aio=SimpleNamespace(models=models)), types_module=FakeTypes,
    )
    with pytest.raises(ScreenshotExtractionError, match=reason):
        asyncio.run(extractor.extract([image("source.jpg")]))


@pytest.mark.parametrize(
    "images, message",
    [
        ([], "스크린샷이 없습니다"),
        ([image("notes.txt", content_type="text/plain")], "지원하지 않는 이미지 형식"),
        ([image("large.jpg", data=b"x" * MAX_INLINE_REQUEST_BYTES)], "20MB"),
    ],
)
def test_rejects_invalid_images_before_network_request(images: list[ScreenshotImage], message: str) -> None:
    models = FakeModels({})
    client = SimpleNamespace(aio=SimpleNamespace(models=models))
    extractor = GeminiScreenshotTranscriptExtractor("", client=client, types_module=FakeTypes)

    with pytest.raises(ScreenshotExtractionError, match=message):
        asyncio.run(extractor.extract(images))

    assert models.request is None


def test_batches_large_image_sets_with_global_indexes_and_overlap(monkeypatch) -> None:
    monkeypatch.setattr("app.services.gemini_screenshot_extractor.MAX_INLINE_REQUEST_BYTES", 10)
    models = FakeModels(
        {
            "sessionTitle": "긴 대화",
            "turns": [
                {"role": "user", "content": "첫 질문", "sourceImageIndexes": [1], "confidence": 1},
                {"role": "assistant", "content": "경계 응답", "sourceImageIndexes": [2], "confidence": 1},
            ],
        },
        [
            {
                "sessionTitle": "긴 대화",
                "turns": [
                    {"role": "assistant", "content": "경계 응답", "sourceImageIndexes": [2], "confidence": 1},
                    {"role": "user", "content": "다음 질문", "sourceImageIndexes": [3], "confidence": 1},
                ],
            }
        ],
    )
    client = SimpleNamespace(aio=SimpleNamespace(models=models))
    extractor = GeminiScreenshotTranscriptExtractor("", client=client, types_module=FakeTypes)
    images = [image("1.jpg", b"1111"), image("2.jpg", b"2222"), image("3.jpg", b"3333")]

    extracted = asyncio.run(extractor.extract(images))
    normalized, _ = normalize_screenshot_extraction(extracted, images, "participant-1", "other")

    assert len(models.requests) == 2
    assert models.requests[1]["contents"][0].parts[1] == ("text", "Image 2: 2.jpg")
    assert models.requests[1]["contents"][0].parts[3] == ("text", "Image 3: 3.jpg")
    assert [turn["content"] for turn in normalized["sessions"][0]["turns"]] == [
        "첫 질문",
        "경계 응답",
        "다음 질문",
    ]


def test_removes_multiple_overlapping_turns_at_image_batch_boundary(monkeypatch) -> None:
    monkeypatch.setattr("app.services.gemini_screenshot_extractor.MAX_INLINE_REQUEST_BYTES", 10)
    question = {"role": "user", "content": "overlap question", "sourceImageIndexes": [2], "confidence": 1}
    answer = {"role": "assistant", "content": "overlap answer", "sourceImageIndexes": [2], "confidence": 1}
    models = FakeModels(
        {"turns": [
            {"role": "user", "content": "first question", "sourceImageIndexes": [1], "confidence": 1},
            question, answer,
        ]},
        [{"turns": [question, answer, {"role": "user", "content": "next question", "sourceImageIndexes": [3], "confidence": 1}]}],
    )
    extractor = GeminiScreenshotTranscriptExtractor(
        "", client=SimpleNamespace(aio=SimpleNamespace(models=models)), types_module=FakeTypes,
    )
    images = [image("1.jpg", b"1111"), image("2.jpg", b"2222"), image("3.jpg", b"3333")]
    extracted = asyncio.run(extractor.extract(images))
    normalized, _ = normalize_screenshot_extraction(extracted, images, "participant-1", "other")
    assert [turn["content"] for turn in normalized["sessions"][0]["turns"]] == [
        "first question", "overlap question", "overlap answer", "next question",
    ]


@pytest.mark.parametrize("difference", [
    {"sourceImageIndexes": [3]},
    {"role": "assistant"},
    {"speakerLabel": "another speaker"},
    {"timestamp": "12:01"},
    {"content": "different text"},
])
def test_batch_merge_preserves_distinct_turns(difference):
    first = {"role": "user", "speakerLabel": "speaker", "content": "repeated text", "timestamp": "12:00", "sourceImageIndexes": [2], "confidence": 1}
    second = {**first, **difference}
    result = GeminiScreenshotTranscriptExtractor._merge_batch_turns([
        {"turns": [first]}, {"turns": [second]},
    ])
    assert result == [first, second]


def test_batch_merge_preserves_provenance_and_lower_confidence_without_mutating_inputs():
    first = {"role": "user", "content": "question", "sourceImageIndexes": [1, 2], "confidence": 1}
    second = {"role": "user", "content": "question", "sourceImageIndexes": [2, 3], "confidence": 0.6}
    result = GeminiScreenshotTranscriptExtractor._merge_batch_turns([
        {"turns": [first]}, {"turns": [second]},
    ])
    assert len(result) == 1
    assert result[0]["sourceImageIndexes"] == [1, 2, 3]
    assert result[0]["confidence"] == 0.6
    assert first["sourceImageIndexes"] == [1, 2]
    assert first["confidence"] == 1


def test_batch_merge_does_not_remove_repeated_turns_within_one_batch():
    turn = {"role": "user", "content": "yes", "sourceImageIndexes": [1], "confidence": 1}
    assert GeminiScreenshotTranscriptExtractor._merge_batch_turns([{"turns": [turn, turn]}]) == [turn, turn]

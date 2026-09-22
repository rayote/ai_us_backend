from app.services.screenshot_evaluation import evaluate_screenshot_extraction


def test_evaluates_aligned_turns_without_returning_transcript_text() -> None:
    expected = {
        "turns": [
            {"role": "user", "content": "첫 질문", "timestamp": None, "sourceImageIndexes": [1]},
            {"role": "assistant", "content": "첫 답변", "timestamp": None, "sourceImageIndexes": [1]},
            {"role": "user", "content": "마지막 질문", "timestamp": None, "sourceImageIndexes": [2]},
        ]
    }
    actual = {
        "turns": [
            {"role": "user", "content": "첫\n질문", "timestamp": None, "sourceImageIndexes": [1]},
            {"role": "assistant", "content": "불필요한 답변", "timestamp": None, "sourceImageIndexes": [1]},
            {"role": "assistant", "content": "첫 답변", "timestamp": None, "sourceImageIndexes": [1]},
            {"role": "user", "content": "마지막 질문", "timestamp": None, "sourceImageIndexes": [2]},
        ]
    }

    report = evaluate_screenshot_extraction(expected, actual)

    assert report["missingTurns"] == 0
    assert report["extraTurns"] == 1
    assert report["alignedPairs"] == 3
    assert report["roleAccuracy"] == 1.0
    assert report["meanTextSimilarity"] == 1.0
    assert all("content" not in item for item in report["alignment"])

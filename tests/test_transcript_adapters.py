import json

from app.services.transcript_adapters import normalize_export
from app.services.transcript_runs import _project_transcript, normalized_to_csv


def test_chatgpt_adapter_follows_active_path() -> None:
    export = [{"id": "conversation-1", "title": "테스트", "current_node": "assistant", "mapping": {"root": {"parent": None, "message": None}, "user": {"parent": "root", "message": {"author": {"role": "user"}, "content": {"parts": ["안녕"]}, "create_time": 1_700_000_000}}, "assistant": {"parent": "user", "message": {"author": {"role": "assistant"}, "content": {"parts": ["반가워"]}, "create_time": 1_700_000_010, "metadata": {"model_slug": "gpt-test"}}}}}]

    name, version, normalized, warnings = normalize_export("chatgpt", "conversations.json", json.dumps(export).encode(), "participant-1")

    assert (name, version, warnings) == ("chatgpt-json", "chatgpt-json-v1", [])
    assert normalized["summary"]["totalTurns"] == 2
    assert normalized["sessions"][0]["turns"][1]["turnMetadata"]["modelSlug"] == "gpt-test"


def test_grok_adapter_preserves_errors_and_csv_rows() -> None:
    export = {"conversations": [{"conversation": {"id": "grok-1", "title": "테스트"}, "responses": [{"response": {"sender": "human", "message": "질문", "create_time": {"$date": {"$numberLong": "1700000000000"}}}}, {"response": {"sender": "assistant", "message": "", "create_time": {"$date": {"$numberLong": "1700000001000"}}, "metadata": {"stream_errors": [{"message": "temporary"}]}}}]}]}

    name, version, normalized, warnings = normalize_export("grok", "export.json", json.dumps(export).encode(), "participant-1")
    csv_text = normalized_to_csv(normalized)

    assert (name, version, warnings) == ("grok-json", "grok-json-v1", [])
    assert normalized["sessions"][0]["turns"][1]["turnMetadata"]["errors"][0]["message"] == "temporary"
    assert "participant-1" in csv_text
    assert "assistant" in csv_text


def test_normalized_result_projects_to_legacy_transcript() -> None:
    normalized = {"sessions": [{"turns": [{"role": "user", "content": "질문"}, {"role": "assistant", "content": "응답"}]}]}

    transcript = _project_transcript(normalized, "adapter-v1", ["경고"])

    assert transcript.status == "parsed"
    assert transcript.parser_version == "adapter-v1"
    assert transcript.plain_text == "user: 질문\nassistant: 응답"
    assert transcript.warnings == ["경고"]

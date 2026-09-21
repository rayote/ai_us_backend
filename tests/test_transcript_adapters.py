import io
import json
import zipfile

import pytest
from app.services.transcript_adapters import UnsupportedTranscriptError, normalize_export
from app.services.transcript_runs import _project_transcript, normalized_to_csv


def test_chatgpt_adapter_follows_active_path() -> None:
    export = [
        {
            "id": "conversation-1",
            "title": "테스트",
            "current_node": "assistant",
            "mapping": {
                "root": {"parent": None, "message": None},
                "user": {
                    "parent": "root",
                    "message": {
                        "author": {"role": "user"},
                        "content": {"parts": ["안녕"]},
                        "create_time": 1_700_000_000,
                    },
                },
                "assistant": {
                    "parent": "user",
                    "message": {
                        "author": {"role": "assistant"},
                        "content": {"parts": ["반가워"]},
                        "create_time": 1_700_000_010,
                        "metadata": {"model_slug": "gpt-test"},
                    },
                },
            },
        }
    ]

    name, version, normalized, warnings = normalize_export(
        "chatgpt", "conversations.json", json.dumps(export).encode(), "participant-1"
    )

    assert (name, version, warnings) == ("chatgpt-json", "chatgpt-json-v1", [])
    assert normalized["summary"]["totalTurns"] == 2
    assert normalized["sessions"][0]["turns"][1]["turnMetadata"]["modelSlug"] == "gpt-test"


def test_chatgpt_adapter_extracts_conversations_json_from_zip() -> None:
    export = [
        {
            "id": "conversation-zip",
            "title": "압축 테스트",
            "current_node": "user",
            "mapping": {
                "user": {
                    "parent": None,
                    "message": {
                        "author": {"role": "user"},
                        "content": {"parts": ["압축 질문"]},
                        "create_time": 1_700_000_000,
                    },
                }
            },
        }
    ]
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipper:
        zipper.writestr("user.json", "{}")
        zipper.writestr("nested/conversations.json", json.dumps(export, ensure_ascii=False).encode())

    name, version, normalized, warnings = normalize_export(
        "chatgpt", "chatgpt-export.zip", archive.getvalue(), "participant-1"
    )

    assert (name, version) == ("chatgpt-json", "chatgpt-json-v1")
    assert normalized["summary"] == {
        "totalSessions": 1,
        "totalTurns": 1,
        "firstActivityTime": "2023-11-15T07:13:20+09:00",
        "lastActivityTime": "2023-11-15T07:13:20+09:00",
    }
    assert warnings == ["ZIP 내부의 nested/conversations.json 파일을 파싱했습니다."]


def test_grok_adapter_preserves_errors_and_csv_rows() -> None:
    export = {
        "conversations": [
            {
                "conversation": {"id": "grok-1", "title": "테스트"},
                "responses": [
                    {
                        "response": {
                            "sender": "human",
                            "message": "질문",
                            "create_time": {"$date": {"$numberLong": "1700000000000"}},
                        }
                    },
                    {
                        "response": {
                            "sender": "assistant",
                            "message": "",
                            "create_time": {"$date": {"$numberLong": "1700000001000"}},
                            "metadata": {"stream_errors": [{"message": "temporary"}]},
                        }
                    },
                ],
            }
        ]
    }

    name, version, normalized, warnings = normalize_export(
        "grok", "export.json", json.dumps(export).encode(), "participant-1"
    )
    csv_text = normalized_to_csv(normalized)

    assert (name, version, warnings) == ("grok-json", "grok-json-v1", [])
    assert normalized["sessions"][0]["turns"][1]["turnMetadata"]["errors"][0]["message"] == "temporary"
    assert "participant-1" in csv_text
    assert "assistant" in csv_text


def test_grok_adapter_accepts_cp949_json() -> None:
    export = {"conversations": [{"conversation": {"id": "grok-1", "title": "한글"}, "responses": []}]}

    _, _, normalized, _ = normalize_export(
        "grok", "grok-export.json", json.dumps(export, ensure_ascii=False).encode("cp949"), "participant-1"
    )

    assert normalized["platform"] == "grok"


def test_grok_adapter_extracts_json_from_zip() -> None:
    export = {"conversations": [{"conversation": {"id": "grok-zip", "title": "zip"}, "responses": []}]}
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipper:
        zipper.writestr("prod-grok-backend.json", json.dumps(export).encode())

    _, _, normalized, warnings = normalize_export("grok", "submission.zip", archive.getvalue(), "participant-1")

    assert normalized["platform"] == "grok"
    assert warnings == ["ZIP 내부의 prod-grok-backend.json 파일을 파싱했습니다."]


def test_gemini_adapter_extracts_takeout_html_from_zip() -> None:
    html = """
    <div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp">
      <div class="mdl-grid">
        <div class="header-cell mdl-cell mdl-cell--12-col"><p>Gemini 앱</p></div>
        <div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">
          오늘 날씨 항목을 검색함<br>2026. 9. 8. 오후 1:54:14 KST<br>
          <p>맑고 선선합니다.</p><p><strong>기온:</strong> 22도</p>
        </div>
        <div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--text-right"></div>
        <div class="content-cell mdl-cell mdl-cell--12-col mdl-typography--caption"><b>제품:</b><br>Gemini 앱</div>
      </div>
    </div>
    """
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipper:
        zipper.writestr("Takeout/내 활동/테이크아웃/내활동.html", "<html></html>")
        zipper.writestr("Takeout/내 활동/Gemini 앱/내활동.html", html)

    name, version, normalized, warnings = normalize_export(
        "gemini", "takeout.zip", archive.getvalue(), "participant-1"
    )

    assert (name, version) == ("gemini-takeout-html", "gemini-takeout-html-v1")
    assert normalized["summary"]["totalSessions"] == 1
    assert normalized["summary"]["totalTurns"] == 2
    assert normalized["sessions"][0]["turns"] == [
        {"turnId": 1, "role": "user", "content": "오늘 날씨", "timestamp": "2026-09-08T13:54:14+09:00"},
        {
            "turnId": 2,
            "role": "assistant",
            "content": "맑고 선선합니다.\n기온: 22도",
            "timestamp": "2026-09-08T13:54:14+09:00",
        },
    ]
    assert warnings == ["ZIP 내부의 Takeout/내 활동/Gemini 앱/내활동.html 파일을 파싱했습니다."]


def test_unsupported_export_reports_actionable_warning() -> None:
    with pytest.raises(UnsupportedTranscriptError, match="parser adapter"):
        normalize_export("claude", "export.zip", b"not-supported", "participant-1")


def test_normalized_result_projects_to_legacy_transcript() -> None:
    normalized = {
        "sessions": [{"turns": [{"role": "user", "content": "질문"}, {"role": "assistant", "content": "응답"}]}]
    }

    transcript = _project_transcript(normalized, "adapter-v1", ["경고"])

    assert transcript.status == "parsed"
    assert transcript.parser_version == "adapter-v1"
    assert transcript.plain_text == "user: 질문\nassistant: 응답"
    assert transcript.warnings == ["경고"]

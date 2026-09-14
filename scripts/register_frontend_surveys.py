from __future__ import annotations

import argparse
import getpass
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_BACKEND_URL = "https://port-0-ai-us-backend-mtmm0sg55da4c824.sel3.cloudtype.app"
DEFAULT_FRONTEND_INDEX = Path(__file__).resolve().parents[2] / "ai_us" / "index.html"


def main() -> int:
    parser = argparse.ArgumentParser(description="Register frontend SURVEY_SETS with the deployed backend.")
    parser.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)
    parser.add_argument("--frontend-index", type=Path, default=DEFAULT_FRONTEND_INDEX)
    parser.add_argument("--survey-round", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    survey_sets = extract_survey_sets(args.frontend_index)
    payloads = [
        {**part, "surveyRound": args.survey_round, "audience": audience}
        for audience, parts in survey_sets.items()
        for part in parts
    ]

    print("Prepared survey definitions:")
    for payload in payloads:
        questions = [question for scale in payload.get("scales", []) for question in scale.get("questions", [])]
        print(f"- {payload['audience']} {payload['surveyVersion']} scales={len(payload.get('scales', []))} questions={len(questions)}")

    if args.dry_run:
        return 0

    username = input("Admin username: ").strip()
    password = getpass.getpass("Admin password: ")
    status, login = post_json(
        args.backend_url,
        "/api/v1/auth/researcher/login",
        {"username": username, "password": password},
    )
    if status != 200:
        print(f"Login failed: HTTP {status} {login}", file=sys.stderr)
        return 1
    if login.get("role") != "admin":
        print(f"Login role is not admin: {login.get('role')}", file=sys.stderr)
        return 1

    token = login["accessToken"]
    success = True
    for payload in payloads:
        status, body = post_json(args.backend_url, "/api/v1/admin/survey-definitions", payload, token)
        detail = body.get("detail", "created") if isinstance(body, dict) else body
        print(f"{payload['audience']} {payload['surveyVersion']}: HTTP {status} {detail}")
        success = success and status in {201, 409}
    return 0 if success else 1


def extract_survey_sets(frontend_index: Path) -> dict[str, list[dict[str, Any]]]:
    html = frontend_index.read_text(encoding="utf-8")
    scripts = "\n".join(re.findall(r"<script[^>]*>([\s\S]*?)</script>", html, flags=re.IGNORECASE))
    match = re.search(r"var\s+SURVEY_SETS\s*=([\s\S]*?);\n\s*var\s+SURVEY_PARTS\s*=", scripts)
    if match is None:
        raise RuntimeError("SURVEY_SETS block was not found in frontend index.html")
    node_code = f"console.log(JSON.stringify(({match.group(1)})));"
    completed = subprocess.run(["node"], input=node_code, text=True, capture_output=True, check=True)
    return json.loads(completed.stdout)


def post_json(
    backend_url: str,
    path: str,
    payload: dict[str, Any],
    token: str | None = None,
) -> tuple[int, dict[str, Any]]:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        backend_url.rstrip("/") + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {"detail": body}
        return error.code, parsed


if __name__ == "__main__":
    raise SystemExit(main())
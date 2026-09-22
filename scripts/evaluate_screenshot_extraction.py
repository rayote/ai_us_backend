from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.services.screenshot_evaluation import evaluate_screenshot_extraction


def _read_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare screenshot transcript JSON without printing text")
    parser.add_argument("expected", type=Path)
    parser.add_argument("actual", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            evaluate_screenshot_extraction(_read_json(args.expected), _read_json(args.actual)),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

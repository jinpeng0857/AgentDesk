"""Export the deterministic FastAPI OpenAPI contract snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.main import app

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPOSITORY_ROOT / "specs" / "openapi" / "openapi.json"


def render_openapi() -> str:
    return f"{json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True)}\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    rendered = render_openapi()

    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            print(f"OpenAPI snapshot is stale: {args.output}")
            return 1
        print(f"OpenAPI snapshot is current: {args.output}")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"Wrote OpenAPI snapshot: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Export the deterministic Tool registry as a reviewable JSON snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.registry import TOOL_REGISTRY

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPOSITORY_ROOT / "specs" / "tool_schemas" / "tools.json"


def render_tool_schemas() -> str:
    payload = {
        "version": "1.0.0",
        "tools": [
            TOOL_REGISTRY[name].model_dump(mode="json")
            for name in sorted(TOOL_REGISTRY)
        ],
    }
    return f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    rendered = render_tool_schemas()

    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            print(f"Tool schema snapshot is stale: {args.output}")
            return 1
        print(f"Tool schema snapshot is current: {args.output}")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"Wrote Tool schema snapshot: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Validate acceptance-ID coverage and executable pytest references."""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from pathlib import Path
from typing import cast

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MATRIX_PATH = REPOSITORY_ROOT / "specs" / "acceptance" / "test_matrix.yaml"
HARD_CONSTRAINT_IDS = tuple(f"H-{number:02d}" for number in range(1, 27))
FAILURE_MODE_IDS = ("F-01a", "F-01b", *(f"F-{number:02d}" for number in range(2, 9)))
REQUIRED_IDS = frozenset((*HARD_CONSTRAINT_IDS, *FAILURE_MODE_IDS))
REQUIRED_FIELDS = frozenset(
    {
        "id",
        "source_constraint",
        "test_level",
        "test_nodes",
        "commands",
        "dependencies",
        "expected_result",
        "evidence_location",
        "ci_entrypoint",
        "manual_entrypoint",
    }
)


def load_test_matrix(path: Path = DEFAULT_MATRIX_PATH) -> dict[str, object]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return {}
    return cast(dict[str, object], loaded)


def _function_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _list_of_strings(value: object) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return cast(list[str], value)


def _test_node_issue(node_id: str, repository_root: Path) -> str | None:
    parts = node_id.split("::")
    if len(parts) != 2 or not parts[0] or not parts[1].startswith("test_"):
        return f"test node must be a file::test_function reference: {node_id}"

    relative_path = Path(parts[0])
    resolved_path = (repository_root / relative_path).resolve()
    try:
        resolved_path.relative_to(repository_root.resolve())
    except ValueError:
        return f"test node escapes the repository root: {node_id}"
    if not resolved_path.is_file() or resolved_path.suffix != ".py":
        return f"test node file does not exist: {node_id}"
    try:
        names = _function_names(resolved_path)
    except (OSError, SyntaxError) as exc:
        return f"test node file cannot be parsed ({type(exc).__name__}): {node_id}"
    if parts[1] not in names:
        return f"test node does not exist: {node_id}"
    return None


def validate_test_matrix_payload(
    matrix: dict[str, object],
    repository_root: Path = REPOSITORY_ROOT,
) -> list[str]:
    issues: list[str] = []
    if matrix.get("version") != "1.0.0":
        issues.append("test matrix version must be 1.0.0")

    rows = matrix.get("acceptance")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return [*issues, "test matrix acceptance must be a list of mappings"]

    acceptance_rows = cast(list[dict[str, object]], rows)
    ids = [row.get("id") for row in acceptance_rows]
    id_counts = Counter(value for value in ids if isinstance(value, str))
    for acceptance_id, count in sorted(id_counts.items()):
        if count > 1:
            issues.append(f"duplicate acceptance id: {acceptance_id}")

    actual_ids = {value for value in ids if isinstance(value, str)}
    for acceptance_id in sorted(REQUIRED_IDS - actual_ids):
        issues.append(f"missing required acceptance id: {acceptance_id}")
    for acceptance_id in sorted(actual_ids - REQUIRED_IDS):
        issues.append(f"unknown acceptance id: {acceptance_id}")

    for row in acceptance_rows:
        row_id = row.get("id")
        if not isinstance(row_id, str):
            issues.append("acceptance row id must be a string")
            continue
        missing = REQUIRED_FIELDS - set(row)
        if missing:
            issues.append(f"{row_id} missing fields: {sorted(missing)}")

        for field in ("source_constraint", "test_level", "expected_result", "evidence_location"):
            value = row.get(field)
            if not isinstance(value, str) or not value.strip():
                issues.append(f"{row_id} field {field} must be a non-empty string")

        test_nodes = _list_of_strings(row.get("test_nodes"))
        if not test_nodes:
            issues.append(f"{row_id} must reference at least one test node")
        else:
            for test_node in test_nodes:
                issue = _test_node_issue(test_node, repository_root)
                if issue:
                    issues.append(f"{row_id}: {issue}")

        commands = _list_of_strings(row.get("commands"))
        if not commands:
            issues.append(f"{acceptance_id} must declare at least one command")

        dependencies = _list_of_strings(row.get("dependencies"))
        if dependencies is None:
            issues.append(f"{acceptance_id} dependencies must be a string list")

        entrypoints = (row.get("ci_entrypoint"), row.get("manual_entrypoint"))
        if not any(isinstance(value, str) and value.strip() for value in entrypoints):
            issues.append(f"{acceptance_id} requires a CI or manual acceptance entrypoint")
        ci_entrypoint = row.get("ci_entrypoint")
        if isinstance(ci_entrypoint, str) and ci_entrypoint.strip():
            workflow = ci_entrypoint.split(" / ", maxsplit=1)[0]
            workflow_path = (repository_root / workflow).resolve()
            try:
                workflow_path.relative_to(repository_root.resolve())
            except ValueError:
                issues.append(f"{acceptance_id} CI entrypoint escapes repository root")
            else:
                if not workflow_path.is_file():
                    issues.append(f"{acceptance_id} CI workflow does not exist: {workflow}")

    return sorted(issues)


def validate_test_matrix(
    matrix_path: Path = DEFAULT_MATRIX_PATH,
    repository_root: Path = REPOSITORY_ROOT,
) -> list[str]:
    try:
        matrix = load_test_matrix(matrix_path)
    except (OSError, yaml.YAMLError) as exc:
        return [f"test matrix cannot be loaded: {type(exc).__name__}"]
    return validate_test_matrix_payload(matrix, repository_root)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate AgentDesk acceptance test matrix")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX_PATH)
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    args = parser.parse_args()

    issues = validate_test_matrix(args.matrix, args.repository_root)
    if issues:
        for issue in issues:
            print(f"- {issue}")
        return 1
    print(f"Acceptance matrix valid: {len(REQUIRED_IDS)} unique IDs with existing tests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

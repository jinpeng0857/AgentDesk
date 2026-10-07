"""The acceptance matrix must remain executable and complete."""

from __future__ import annotations

import copy
import importlib
from pathlib import Path
from typing import Any


def _module() -> Any:
    return importlib.import_module("scripts.maintenance.validate_test_matrix")


def test_acceptance_matrix_covers_every_hard_constraint_and_failure_mode() -> None:
    module = _module()

    assert module.validate_test_matrix() == []


def test_acceptance_matrix_detects_duplicate_ids_and_missing_test_nodes() -> None:
    module = _module()
    matrix = copy.deepcopy(module.load_test_matrix())
    matrix["acceptance"].append(copy.deepcopy(matrix["acceptance"][0]))
    matrix["acceptance"][1]["test_nodes"] = [
        "tests/unit/missing.py::test_missing"
    ]

    issues = module.validate_test_matrix_payload(matrix, Path.cwd())

    assert any("duplicate acceptance id" in issue for issue in issues)
    assert any("test node file does not exist" in issue for issue in issues)

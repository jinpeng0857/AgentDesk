from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

FORBIDDEN_ROOTS = {"backend", "fastapi", "sqlalchemy", "redis", "arq"}
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def scan_import_roots(roots: Iterable[Path], forbidden: set[str]) -> list[str]:
    violations: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                module_names: list[str] = []
                if isinstance(node, ast.Import):
                    module_names.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    module_names.append(node.module)

                for module_name in module_names:
                    import_root = module_name.split(".", maxsplit=1)[0]
                    if import_root in forbidden:
                        relative_path = path.relative_to(root).as_posix()
                        violations.append(
                            f"{relative_path}:{getattr(node, 'lineno', 0)} imports {import_root}"
                        )
    return violations


def test_scanner_reports_forbidden_backend_import(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text("import backend\n", encoding="utf-8")

    assert scan_import_roots((tmp_path,), FORBIDDEN_ROOTS) == [
        "sample.py:1 imports backend"
    ]


def test_scanner_allows_domain_import(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text("from agents import schemas\n", encoding="utf-8")

    assert scan_import_roots((tmp_path,), FORBIDDEN_ROOTS) == []


def test_domain_layers_do_not_import_service_or_infrastructure() -> None:
    domain_roots = tuple(REPOSITORY_ROOT / name for name in ("agents", "tools", "skills"))

    assert scan_import_roots(domain_roots, FORBIDDEN_ROOTS) == []

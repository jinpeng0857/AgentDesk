from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

import yaml

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
    domain_roots = tuple(
        REPOSITORY_ROOT / "src" / name for name in ("agents", "tools", "skills")
    )

    assert scan_import_roots(domain_roots, FORBIDDEN_ROOTS) == []


def test_compose_services_configuration() -> None:
    compose_path = REPOSITORY_ROOT / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = compose["services"]

    assert set(services) == {"postgres", "redis", "api", "worker", "frontend"}
    for infra_name in ("postgres", "redis"):
        infra = services[infra_name]
        assert infra["image"]
        assert not infra["image"].endswith(":latest")
        assert infra["healthcheck"]["test"]
        assert "build" not in infra

    postgres = services["postgres"]
    assert "postgres_data:/var/lib/postgresql/data" in postgres["volumes"]
    assert "postgres_data" in compose["volumes"]

    for app_name in ("api", "worker"):
        app = services[app_name]
        assert app["build"]["context"] == "."
        assert app["build"]["dockerfile"] == "src/backend/Dockerfile"
        assert app["command"]
        assert app["depends_on"]["postgres"]["condition"] == "service_healthy"
        assert app["depends_on"]["redis"]["condition"] == "service_healthy"

    assert services["api"]["command"] != services["worker"]["command"]


def test_api_layer_does_not_import_graph_layer() -> None:
    api_root = (REPOSITORY_ROOT / "src" / "backend" / "api",)
    forbidden = {"backend.graph", "graph"}
    assert scan_import_roots(api_root, forbidden) == []

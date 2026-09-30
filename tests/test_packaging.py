"""Paketleme ve taşınabilirlik testleri.

Buradaki testlerin varlık sebebi gerçek bir hata: arayüz eklendiğinde fastapi,
uvicorn, python-multipart, httpx ve openpyxl kullanılmaya başlandı ama
``pyproject.toml``a eklenmedi. Geliştirme ortamında zaten kurulu oldukları için
fark edilmedi; temiz bir makinede ``dwhflow serve`` çökerdi. Bu testler o
sapmanın sessizce tekrarlanmasını engeller.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "dwhflow"
PYPROJECT = ROOT / "pyproject.toml"

#: import adı → dağıtım (pip) adı. Çoğu aynı; farklı olanlar burada.
MODULE_TO_DIST = {
    "yaml": "pyyaml",
    "multipart": "python-multipart",
    "python_multipart": "python-multipart",
    "dateutil": "python-dateutil",
}


@pytest.fixture(scope="module")
def pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def declared_distributions(pyproject: dict) -> set[str]:
    names: set[str] = set()
    groups = [pyproject["project"]["dependencies"]]
    groups += list(pyproject["project"].get("optional-dependencies", {}).values())
    for group in groups:
        for spec in group:
            name = spec.split(";")[0].split("[")[0]
            for separator in (">=", "==", "<=", "~=", "!=", ">", "<"):
                name = name.split(separator)[0]
            names.add(name.strip().lower().replace("_", "-"))
    return names


def imported_modules() -> set[str]:
    """Kaynak ağacındaki tüm üçüncü parti üst seviye import'lar.

    ``examples/`` altındaki .py dosyaları paket kodu değil, okunacak örnek dokümanlardır
    (ör. Airflow DAG'ı); hiçbir zaman içe aktarılmaz, bu yüzden taramaya girmez.
    """
    found: set[str] = set()
    examples = SRC / "examples"
    for path in SRC.rglob("*.py"):
        if examples in path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:  # göreli import değil
                    found.add(node.module.split(".")[0])
    return {
        module
        for module in found
        if module not in sys.stdlib_module_names and module != "dwhflow"
    }


class TestDependencyDeclaration:
    def test_every_import_is_declared(self, pyproject):
        declared = declared_distributions(pyproject)
        missing = {
            module
            for module in imported_modules()
            if MODULE_TO_DIST.get(module, module).lower() not in declared
        }
        assert not missing, (
            f"pyproject.toml'da tanımlı olmayan bağımlılık(lar): {sorted(missing)}. "
            "Temiz bir kurulumda çalışmaz."
        )

    def test_ui_dependencies_are_core_not_optional(self, pyproject):
        """`serve` varsayılan kurulumla çalışmalı; arayüz opsiyonel ekstra olmamalı."""
        core = {
            spec.split(">=")[0].strip().lower() for spec in pyproject["project"]["dependencies"]
        }
        for package in ("fastapi", "uvicorn", "python-multipart", "httpx", "openpyxl"):
            assert package in core, f"{package} çekirdek bağımlılıklarda olmalı"

    def test_python_floor_declared(self, pyproject):
        assert pyproject["project"]["requires-python"] == ">=3.11"

    def test_console_script_points_at_cli(self, pyproject):
        assert pyproject["project"]["scripts"]["dwhflow"] == "dwhflow.cli:app"


class TestPackagedData:
    """Arayüz, motor, istemler ve örnekler paketin içinde olmalı — wheel'a girmezlerse
    kurulan sürümde ne `serve` ne `convert` çalışır."""

    def test_static_assets_inside_package(self):
        static = SRC / "static"
        assert (static / "index.html").is_file() and (static / "css" / "app.css").is_file()
        assert {p.stem for p in (static / "js" / "core").glob("*.js")} >= {"api", "dom", "router", "store", "ui"}
        assert (static / "js" / "main.js").is_file()

    def test_every_route_has_a_screen_module(self):
        """Yönlendiricideki her ekranın modülü pakette olmalı (yoksa ekran açılmaz)."""
        import re

        router = (SRC / "static" / "js" / "core" / "router.js").read_text(encoding="utf-8")
        screens = set(re.findall(r"screen: '([a-z-]+)'", router))
        present = {p.stem for p in (SRC / "static" / "js" / "screens").glob("*.js")}
        assert screens and not (screens - present), f"modülü olmayan ekranlar: {sorted(screens - present)}"

    def test_no_inline_script_or_handlers(self):
        """CSP script-src 'self': satır içi betik ve on* öznitelikleri çalışmaz."""
        html = (SRC / "static" / "index.html").read_text(encoding="utf-8")
        assert "<script>" not in html and " onclick=" not in html

    def test_old_odi_ui_removed(self):
        assert not (SRC / "layout").exists()
        assert not (SRC / "static" / "app.js").exists()

    def test_engine_bundled_and_pinned(self):
        import hashlib
        import json

        lock = json.loads((SRC / "engine" / "engine.lock.json").read_text(encoding="utf-8"))
        archive = SRC / "engine" / lock["file"]
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == lock["sha256"]
        assert lock["source"].startswith("https://github.com/tt-a1i/archify")

    def test_prompts_and_locale_inside_package(self):
        assert (SRC / "llm" / "prompts" / "extract_flows.v1.yaml").is_file()
        assert (SRC / "render" / "locale_tr.json").is_file()

    def test_examples_inside_package(self):
        demo = SRC / "examples" / "demo_bank_dwh"
        assert {path.name for path in demo.glob("*.yaml")} == {
            "systems.yaml", "datasets.yaml", "processes.yaml", "edges.yaml",
        }
        samples = {p.suffix for p in (SRC / "examples" / "ornek_dokumanlar").iterdir()}
        assert samples == {".xlsx", ".docx", ".pdf", ".sql", ".jil", ".txt"}

    def test_no_external_urls_in_frontend(self):
        """Çevrimdışı çalışma sözü: arayüz CDN'den dosya çekmemeli."""
        offenders = []
        for path in (SRC / "static").rglob("*"):
            if path.suffix not in {".html", ".js", ".css"}:
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                for marker in ("https://", "http://"):
                    if marker in line and "localhost" not in line and "127.0.0.1" not in line:
                        if "www.w3.org" in line:  # SVG ad alanı; indirme yapmaz
                            continue
                        offenders.append(f"{path.name}: {line.strip()[:70]}")
        assert not offenders, f"Arayüzde dış bağlantı var: {offenders}"


class TestDemoDiscovery:
    def test_demo_path_resolves_from_package(self):
        from dwhflow.cli import _demo_path

        demo = _demo_path()
        assert demo is not None
        assert demo.is_dir()

    def test_demo_path_prefers_package_copy(self):
        from dwhflow.cli import _demo_path

        assert _demo_path() == SRC / "examples" / "demo_bank_dwh"


class TestDoctor:
    def test_doctor_passes_in_this_environment(self):
        from typer.testing import CliRunner

        from dwhflow.cli import app

        result = CliRunner().invoke(app, ["doctor"])
        assert result.exit_code == 0, result.output
        assert "Her şey yerinde" in result.output

    def test_doctor_lists_every_requirement(self):
        from typer.testing import CliRunner

        from dwhflow.cli import _REQUIREMENTS, app

        output = CliRunner().invoke(app, ["doctor"]).output
        for module, _, _ in _REQUIREMENTS:
            assert module in output

    def test_version_flag(self):
        from typer.testing import CliRunner

        from dwhflow import __version__
        from dwhflow.cli import app

        result = CliRunner().invoke(app, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output

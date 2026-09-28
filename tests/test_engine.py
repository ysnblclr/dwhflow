"""Archify motor köprüsü: sabitleme, bütünlük, kurulum, Node algılama, finalize."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from dwhflow.render.engine import (
    BASE_ENV,
    ENGINE_DIR,
    Engine,
    EngineError,
    FinalizeResult,
    node_version,
    read_lock,
    sha256_of,
)

NODE_OK = (node_version()[1] or 0) >= 18


def fake_lock(engine: Engine, archive: Path) -> Engine:
    """Kilidi küçük bir sahte zip'e sabitler (gerçek 2 MB'lık paketi açmadan)."""
    engine.lock = {**engine.lock, "file": archive.name, "sha256": sha256_of(archive)}
    return engine


@pytest.fixture
def tiny_archive(tmp_path):
    archive = tmp_path / "tiny.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("archify/bin/archify.mjs", "console.log('ok')")
    return archive


class TestLock:
    def test_bundled_archive_matches_lock(self):
        lock = read_lock()
        archive = ENGINE_DIR / lock["file"]
        assert archive.is_file()
        assert archive.stat().st_size == lock["bytes"]
        assert sha256_of(archive) == lock["sha256"]

    def test_lock_pins_version_and_license(self):
        lock = read_lock()
        assert lock["name"] == "archify" and lock["version"] == "3.0.1"
        assert lock["license"] == "MIT" and lock["node"] == ">=18"

    def test_update_check_always_disabled(self):
        assert BASE_ENV["ARCHIFY_UPDATE_CHECK_DISABLED"] == "1"
        assert Engine(install_dir=Path("x"))._env()["ARCHIFY_UPDATE_CHECK_DISABLED"] == "1"


class TestInstall:
    def test_install_and_idempotent(self, tmp_path, tiny_archive):
        engine = fake_lock(Engine(install_dir=tmp_path / "engine"), tiny_archive)
        engine.ensure_installed(tiny_archive)
        assert engine.cli.is_file() and engine.is_installed()
        marker = engine.marker.stat().st_mtime_ns
        engine.ensure_installed(tiny_archive)
        assert engine.marker.stat().st_mtime_ns == marker

    def test_tampered_archive_rejected(self, tmp_path, tiny_archive):
        engine = fake_lock(Engine(install_dir=tmp_path / "engine"), tiny_archive)
        with zipfile.ZipFile(tiny_archive, "a") as zf:
            zf.writestr("archify/kotu.js", "process.exit(1)")
        with pytest.raises(EngineError, match="SHA-256"):
            engine.ensure_installed(tiny_archive)
        assert not engine.install_dir.exists()

    def test_zip_slip_rejected(self, tmp_path):
        archive = tmp_path / "evil.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../../kacak.txt", "x")
        engine = fake_lock(Engine(install_dir=tmp_path / "a" / "engine"), archive)
        with pytest.raises(EngineError, match="güvensiz"):
            engine.ensure_installed(archive)
        assert not (tmp_path / "kacak.txt").exists()
        assert not list((tmp_path / "a").glob(".archify-*"))  # yarım kurulum temizlendi

    def test_stale_install_replaced(self, tmp_path, tiny_archive):
        engine = fake_lock(Engine(install_dir=tmp_path / "engine"), tiny_archive)
        engine.install_dir.mkdir(parents=True)
        engine.marker.write_text(json.dumps({"sha256": "eski"}), encoding="utf-8")
        assert not engine.is_installed()
        engine.ensure_installed(tiny_archive)
        assert engine.is_installed()

    def test_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DWHFLOW_ENGINE_DIR", str(tmp_path / "burada"))
        assert Engine().install_dir == tmp_path / "burada"


class TestNode:
    def test_missing_node_message(self, tmp_path):
        engine = Engine(install_dir=tmp_path, node=str(tmp_path / "yok" / "node.exe"))
        with pytest.raises(EngineError):
            engine.node()

    def test_version_parsing(self):
        path, major = node_version()
        if path is None:
            pytest.skip("Node.js yok")
        assert isinstance(major, int)


class TestResult:
    def test_skipped_browser_still_delivered(self):
        result = FinalizeResult(status="skipped",
                                gates={"validate": "pass", "deliver": "pass", "check": "pass",
                                       "browser-check": "skipped"})
        assert result.delivered and not result.browser_verified
        assert "atlandı" in result.summary_tr()

    def test_failed_gate_named(self):
        result = FinalizeResult(status="fail", gates={"validate": "pass", "deliver": "fail"})
        assert not result.delivered and "deliver" in result.summary_tr()


@pytest.mark.skipif(not NODE_OK, reason="Node.js 18+ yok")
class TestFinalizeIntegration:
    """Gerçek motorla tek bir küçük diyagram (Node varsa)."""

    def test_small_diagram(self, tmp_path, demo_model):
        from dwhflow.render.compile import compile_slice
        from dwhflow.render.slices import Slice

        piece = Slice(id="kucuk", title="Küçük", datasets=["src_cb_account", "stg_account", "ods_account"])
        spec_path = tmp_path / "kucuk.json"
        spec_path.write_text(json.dumps(compile_slice(demo_model, piece, output="kucuk.html"),
                                        ensure_ascii=False), encoding="utf-8")
        result = Engine().finalize("architecture", spec_path, tmp_path / "kucuk.html")
        assert result.delivered, result.as_dict()
        html = result.html.read_text(encoding="utf-8")
        assert 'lang="tr"' in html and "STG_ACCOUNT" in html
        assert "Dışa aktar" in html  # Türkçe görüntüleyici paketi gömülü

    def test_translation_pack_complete(self, tmp_path, demo_model):
        """Kısmi çeviri Archify'ın stderr'ine uyarı basar ve yapılandırılmış hata
        tanılarını bozar ('internal/unclassified'). Paket tam olmalı."""
        import subprocess

        from dwhflow.render.compile import compile_slice
        from dwhflow.render.slices import Slice

        engine = Engine()
        engine.ensure_installed()
        spec_path = tmp_path / "t.json"
        spec_path.write_text(json.dumps(compile_slice(demo_model, Slice(id="t", title="T",
                             datasets=["stg_account"]), output="t.html"), ensure_ascii=False),
                             encoding="utf-8")
        completed = subprocess.run([engine.node(), str(engine.cli), "validate", "architecture",
                                    spec_path.name], cwd=tmp_path, capture_output=True, text=True,
                                   encoding="utf-8", env=engine._env())
        assert "fall back to English" not in completed.stderr

    def test_standard_quality_label_repaired(self, tmp_path, demo_model):
        """Standart kalitede tek sütunlu diyagramda etiket kutuyla çakışıyordu; motorun
        önerdiği labelAt uygulanıp ayrı klasörde yeniden denenir."""
        from dwhflow.convert import _render_one
        from dwhflow.render.slices import Slice

        piece = Slice(id="kucuk", title="Küçük", datasets=["src_cb_account", "stg_account", "ods_account"])
        item = _render_one(Engine(), demo_model, piece, tmp_path, [], None, "standard")
        assert item["html"], item
        assert item["repairs"] >= 1 and len(item["attempts"]) >= 2
        assert item["html"].startswith("diagrams/kucuk/onarim-")

    def test_invalid_spec_never_passes(self, tmp_path):
        spec_path = tmp_path / "bozuk.json"
        spec_path.write_text('{"diagram_type": "architecture"}', encoding="utf-8")
        result = Engine().finalize("architecture", spec_path, tmp_path / "bozuk.html", quality="standard")
        assert result.status != "pass" and not result.delivered and result.html is None


def test_engine_folder_holds_only_pinned_files():
    assert sorted(p.name for p in ENGINE_DIR.iterdir() if p.is_file()) == [
        read_lock()["file"], "engine.lock.json"]

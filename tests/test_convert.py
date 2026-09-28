"""Dönüştürme hattı: dokümanlar → model → diyagramlar → galeri."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dwhflow.convert import RunOptions, convert
from dwhflow.render.engine import EngineError, FinalizeResult, node_version
from dwhflow.store import load_model

SAMPLES = Path(__file__).resolve().parents[1] / "src" / "dwhflow" / "examples" / "ornek_dokumanlar"
PASS = {"validate": "pass", "deliver": "pass", "check": "pass", "browser-check": "pass"}


class FakeEngine:
    """Node gerektirmeyen motor: spec'i kaydeder, sonucu senaryoya göre döner."""

    lock = {"name": "archify", "version": "3.0.1"}

    def __init__(self, outcome=None, broken=False):
        self.outcome = outcome or (lambda spec, quality: "pass")
        self.broken = broken
        self.calls: list[tuple[str, str]] = []

    def ensure_installed(self):
        if self.broken:
            raise EngineError("Node.js bulunamadı.")

    def node(self):
        return "node"

    def finalize(self, diagram_type, spec_path, html_path, *, quality="showcase", env=None):
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        self.calls.append((spec_path.parent.name, quality))
        status = self.outcome(spec, quality)
        if status == "pass":
            html_path.write_text("<html lang='tr'></html>", encoding="utf-8")
            return FinalizeResult(status="pass", gates=dict(PASS), html=html_path, quality=quality)
        if status == "skipped":
            html_path.write_text("<html></html>", encoding="utf-8")
            gates = {**PASS, "browser-check": "skipped"}
            return FinalizeResult(status="skipped", gates=gates, html=html_path, quality=quality)
        return FinalizeResult(status="fail", gates={"validate": "fail"}, quality=quality)


def samples():
    return sorted(SAMPLES.iterdir())


class TestConvert:
    def test_model_only(self, tmp_path):
        report = convert(samples(), tmp_path, options=RunOptions(render=False))
        assert report["ingest"]["stats"]["datasets"] == 12
        assert report["diagrams"] == [] and any("--no-render" in n for n in report["notes"])
        assert (tmp_path / "index.html").is_file() and (tmp_path / "dwhflow-run.json").is_file()
        assert len(load_model(tmp_path / "model").datasets) == 12

    def test_each_diagram_in_own_folder(self, tmp_path):
        """Regresyon: Archify çıktı dizinini kilitler; paylaşılan klasör 'concurrent-attempt' verdi."""
        engine = FakeEngine()
        report = convert(samples(), tmp_path, engine=engine,
                         options=RunOptions(paths=[("SRC_KART_ISLEM", "BDDK_PORTAL")]))
        ids = [d["id"] for d in report["diagrams"]]
        assert ids[0].startswith("yol-") and "genel-akis" in ids and any(i.startswith("batch") for i in ids)
        for item in report["diagrams"]:
            assert item["html"] == f"diagrams/{item['id']}/{item['id']}.html"
            assert (tmp_path / item["html"]).is_file()
        assert len({folder for folder, _ in engine.calls}) == len(ids)

    def test_fallback_to_standard_in_subfolder(self, tmp_path):
        engine = FakeEngine(lambda spec, quality: "pass" if quality == "standard" else "fail")
        report = convert(samples(), tmp_path, engine=engine, options=RunOptions(jobs=1))
        item = report["diagrams"][0]
        assert item["quality"] == "standard" and item["html"].endswith(f"/standard/{item['id']}.html")
        assert [a["quality"] for a in item["attempts"]] == ["showcase", "standard"]

    def test_failure_is_never_reported_as_ready(self, tmp_path):
        report = convert(samples(), tmp_path, engine=FakeEngine(lambda spec, quality: "fail"))
        assert all(d["html"] is None and d["status"] == "fail" for d in report["diagrams"])
        gallery = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert "Üretilemedi" in gallery

    def test_skipped_browser_check_shown_honestly(self, tmp_path):
        report = convert(samples(), tmp_path, engine=FakeEngine(lambda spec, quality: "skipped"))
        assert all(d["html"] and not d["browser_verified"] for d in report["diagrams"])
        assert "Tarayıcı kontrolü atlandı" in (tmp_path / "index.html").read_text(encoding="utf-8")

    def test_missing_node_reported_not_crashed(self, tmp_path):
        report = convert(samples(), tmp_path, engine=FakeEngine(broken=True))
        assert report["diagrams"] == [] and any("Node.js" in n for n in report["notes"])
        assert (tmp_path / "index.html").is_file()

    def test_one_broken_diagram_does_not_stop_others(self, tmp_path):
        def outcome(spec, quality):
            if spec["meta"]["title"].startswith("Genel"):
                raise RuntimeError("beklenmeyen")
            return "pass"
        report = convert(samples(), tmp_path, engine=FakeEngine(outcome))
        statuses = {d["id"]: d["status"] for d in report["diagrams"]}
        assert statuses["genel-akis"] == "error" and "pass" in statuses.values()

    def test_bad_path_request_becomes_note(self, tmp_path):
        report = convert(samples(), tmp_path, engine=FakeEngine(),
                         options=RunOptions(paths=[("YOK_BOYLE", "BDDK_PORTAL")]))
        assert any("yol diyagramı üretilemedi" in n for n in report["notes"])
        assert report["diagrams"]

    def test_duplicate_requests_get_unique_ids(self, tmp_path):
        report = convert(samples(), tmp_path, engine=FakeEngine(), options=RunOptions(
            paths=[("SRC_KART_ISLEM", "BDDK_PORTAL")] * 2, auto=False))
        ids = [d["id"] for d in report["diagrams"]]
        assert len(ids) == 2 and len(set(ids)) == 2

    def test_empty_documents(self, tmp_path):
        doc = tmp_path / "bos.txt"
        doc.write_text("Bu dokümanda tablo adı geçmiyor.", encoding="utf-8")
        report = convert([doc], tmp_path / "out", engine=FakeEngine())
        assert report["diagrams"] == [] and any("hiç tablo" in n for n in report["notes"])

    def test_gallery_is_offline(self, tmp_path):
        convert(samples(), tmp_path, engine=FakeEngine())
        gallery = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert "http://" not in gallery and "https://" not in gallery
        assert "STTM_Kart_Islemleri.xlsx" in gallery


@pytest.mark.skipif((node_version()[1] or 0) < 18, reason="Node.js 18+ yok")
def test_real_engine_sample_documents(tmp_path):
    """Uçtan uca: 6 biçimde 6 doküman → gerçek Archify ile doğrulanmış diyagramlar."""
    report = convert(samples(), tmp_path, options=RunOptions(paths=[("SRC_KART_ISLEM", "BDDK_PORTAL")]))
    assert report["diagrams"]
    for item in report["diagrams"]:
        assert item["html"], item
        assert item["gates"]["validate"] == item["gates"]["deliver"] == item["gates"]["check"] == "pass"
        html = (tmp_path / item["html"]).read_text(encoding="utf-8")
        assert 'lang="tr"' in html
    path = report["diagrams"][0]
    assert path["kind"] == "path"
    assert "BDDK_PORTAL" in (tmp_path / path["html"]).read_text(encoding="utf-8")

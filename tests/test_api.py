"""Web uygulaması: güvenlik korkulukları, yükle → dönüştür → sonuç, ayarlar."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dwhflow.api import create_app
from dwhflow.api.app import _host_name, _parse_paths

from .test_convert import FakeEngine

SAMPLES = Path(__file__).resolve().parents[1] / "src" / "dwhflow" / "examples" / "ornek_dokumanlar"
H = {"X-DWHFlow": "1"}


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "veri", engine=FakeEngine(), allowed_hosts={"testserver"})
    with TestClient(app) as test_client:
        yield test_client


def upload(client, *names, **form):
    files = [("files", (name, (SAMPLES / name).read_bytes())) for name in names]
    return client.post("/api/runs", files=files, data=form, headers=H)


def wait(client, run_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/api/runs/{run_id}").json()
        if status["state"] in ("done", "error"):
            return status
        time.sleep(0.1)
    raise AssertionError("dönüştürme bitmedi")


class TestSecurity:
    def test_foreign_host_rejected(self, tmp_path):
        """DNS rebinding: dış bir ad 127.0.0.1'e çözülse bile Host başlığı reddedilir."""
        app = create_app(tmp_path, engine=FakeEngine())
        with TestClient(app, base_url="http://kotu.example") as c:
            assert c.get("/api/health").status_code == 421

    def test_local_hosts_accepted(self, tmp_path):
        app = create_app(tmp_path, engine=FakeEngine())
        with TestClient(app, base_url="http://127.0.0.1:8765") as c:
            for host in ("127.0.0.1:8765", "localhost:8765", "[::1]:8765", "localhost"):
                assert c.get("/api/health", headers={"host": host}).status_code == 200, host
            assert c.get("/api/health", headers={"host": "127.0.0.1.evil.example"}).status_code == 421

    def test_testserver_not_allowed_by_default(self, tmp_path):
        with TestClient(create_app(tmp_path, engine=FakeEngine())) as c:
            assert c.get("/api/health").status_code == 421

    def test_mutation_requires_header(self, client):
        """CSRF: başka bir sitenin formu özel başlık gönderemez."""
        files = [("files", ("a.txt", b"STG_A -> ODS_A"))]
        assert client.post("/api/runs", files=files).status_code == 403
        assert client.put("/api/settings", json={"quality": "standard"}).status_code == 403
        assert client.post("/api/llm/test").status_code == 403

    def test_headers(self, client):
        response = client.get("/api/health")
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"

    def test_no_cors(self, client):
        response = client.options("/api/runs", headers={"Origin": "http://kotu.example",
                                                        "Access-Control-Request-Method": "POST"})
        assert "access-control-allow-origin" not in response.headers

    @pytest.mark.parametrize("header, expected", [
        ("127.0.0.1:8765", "127.0.0.1"), ("localhost", "localhost"), ("[::1]:80", "[::1]"),
        ("LOCALHOST:1", "localhost"), ("127.0.0.1.evil.example", "127.0.0.1.evil.example"),
    ])
    def test_host_parsing(self, header, expected):
        assert _host_name(header) == expected


class TestRuns:
    def test_health(self, client):
        body = client.get("/api/health").json()
        assert body["engine"]["version"] == "3.0.1"
        assert ".docx" in body["accepted"] and body["llm"]["enabled"] is False

    def test_upload_convert_and_open(self, client):
        response = upload(client, "STTM_Kart_Islemleri.xlsx", "kart_mart_yukleme.sql",
                          title="Kart", paths="STG_KART_ISLEM > MART_KART_OZET")
        assert response.status_code == 200, response.text
        status = wait(client, response.json()["id"])
        assert status["state"] == "done", status
        report = status["report"]
        assert report["title"] == "Kart" and report["inputs"] == ["STTM_Kart_Islemleri.xlsx",
                                                                  "kart_mart_yukleme.sql"]
        assert report["diagrams"][0]["kind"] == "path"
        run_id = status["id"]
        gallery = client.get(f"/runs/{run_id}/index.html")
        assert gallery.status_code == 200 and "Kart" in gallery.text
        assert client.get(f"/runs/{run_id}/{report['diagrams'][0]['html']}").status_code == 200
        assert [r["id"] for r in client.get("/api/runs").json()] == [run_id]
        assert [p["stage"] for p in status["progress"]][-1] == "bitti"

    def test_bad_extension_rejected_and_cleaned(self, client):
        response = client.post("/api/runs", files=[("files", ("x.exe", b"MZ"))], headers=H)
        assert response.status_code == 400 and "desteklenmeyen" in response.json()["detail"]
        assert client.get("/api/runs").json() == []

    def test_bad_quality(self, client):
        assert upload(client, "operasyon_notlari.txt", quality="ultra").status_code == 400

    def test_llm_requires_enabled_setting(self, client):
        response = upload(client, "operasyon_notlari.txt", use_llm="true")
        assert response.status_code == 400 and "Ayarlar" in response.json()["detail"]

    def test_external_llm_blocked_at_upload(self, client):
        client.put("/api/settings", json={"llm": {"enabled": True,
                                                  "base_url": "https://api.example.com/v1"}},
                   headers=H)
        response = upload(client, "operasyon_notlari.txt", use_llm="true")
        assert response.status_code == 400 and "yerel" in response.json()["detail"]

    def test_delete(self, client):
        run_id = upload(client, "operasyon_notlari.txt").json()["id"]
        wait(client, run_id)
        assert client.delete(f"/api/runs/{run_id}", headers=H).status_code == 200
        assert client.get(f"/api/runs/{run_id}").status_code == 404

    def test_invalid_run_id(self, client):
        assert client.get("/api/runs/..%2F..%2Fetc").status_code in (400, 404)
        assert client.delete("/api/runs/abc", headers=H).status_code == 400


class TestSettings:
    def test_roundtrip(self, client):
        response = client.put("/api/settings", json={"quality": "standard",
                                                     "llm": {"model": "qwen3:14b"}}, headers=H)
        assert response.status_code == 200
        settings = client.get("/api/settings").json()
        assert settings["quality"] == "standard" and settings["llm"]["model"] == "qwen3:14b"

    def test_unknown_rejected(self, client):
        assert client.put("/api/settings", json={"x": 1}, headers=H).status_code == 400

    def test_llm_test_uses_saved_config_only(self, client, monkeypatch):
        """Regresyon (#14): istek gövdesindeki URL ile sunucu keyfi adrese istek atabiliyordu."""
        seen = []

        def fake_probe(self):
            seen.append(self.config.base_url)
            return {"ok": True}

        monkeypatch.setattr("dwhflow.llm.client.LLMClient.probe", fake_probe)
        client.post("/api/llm/test", json={"base_url": "http://169.254.169.254/"}, headers=H)
        assert seen == ["http://localhost:11434/v1"]


class TestStatic:
    def test_index_served(self, client):
        response = client.get("/")
        assert response.status_code == 200 and "DWHFlow" in response.text

    def test_assets_served(self, client):
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/assets/app.css").status_code == 200


def test_parse_paths():
    assert _parse_paths("A > B\nC -> D\nE → F\nbozuk\n > G") == [("A", "B"), ("C", "D"), ("E", "F")]
    assert len(_parse_paths("\n".join(f"A{i}>B" for i in range(30)))) == 10

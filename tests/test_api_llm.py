"""Yapay zekâ uçları ve LLM'in proje hattına bağlanması (sahte OpenAI uyumlu sunucuyla)."""

from __future__ import annotations

import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from dwhflow import auth
from dwhflow.api import create_app
from dwhflow.llm import LLMClient
from dwhflow.model import EdgeKind, Status

from .test_convert import FakeEngine

H = {"X-DWHFlow": "1"}
PASSWORD = "Guclu-Parola1"
TEXT = ("Risk ekibinin sabah baktığı RPT_KART_PANO, MART_KART_OZET üzerinde çalışan gece "
        "raporlamasının çıktısıdır.")


def reply(payload):
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]})


class FakeLLM:
    """Gönderilen metne göre yanıt veren sahte uç; çağrıları kaydeder."""

    def __init__(self, confidence=0.9):
        self.calls = []
        self.confidence = confidence

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen3:8b"}]})
        body = json.loads(request.content)
        self.calls.append(body)
        user = body["messages"][-1]["content"]
        if "RPT_KART_PANO" in user:
            return reply({"schema": "dwhflow.extract/2", "flows": [
                {"source": "MART_KART_OZET", "target": "RPT_KART_PANO", "evidence": TEXT,
                 "confidence": self.confidence}]})
        return reply({"schema": "dwhflow.extract/2"})

    def factory(self, config):
        return LLMClient(config, transport=httpx.MockTransport(self.handler))


@pytest.fixture(autouse=True)
def fast_hash(monkeypatch):
    monkeypatch.setattr(auth, "PBKDF2_ITERATIONS", 1000)


@pytest.fixture
def llm():
    return FakeLLM()


@pytest.fixture
def client(tmp_path, llm):
    app = create_app(tmp_path / "veri", engine=FakeEngine(), allowed_hosts={"testserver"}, llm_factory=llm.factory)
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        assert c.post("/api/auth/setup", json={"username": "yonetici", "password": PASSWORD}, headers=H).status_code == 200
        yield c


def wait(client, job_id):
    for _ in range(600):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError("iş bitmedi")


@pytest.fixture
def kart(client):
    body = client.post("/api/projects", json={"name": "Kart", "sample": "kart"}, headers=H).json()
    assert wait(client, body["job"]["id"])["state"] == "done"
    return body["slug"]


class TestProjectLLM:
    def test_build_with_llm_adds_draft_flow(self, client, kart, llm):
        job = client.post(f"/api/projects/{kart}/build", json={"llm": True}, headers=H).json()
        assert wait(client, job["id"])["state"] == "done"
        names = {d["name"] for d in client.get(f"/api/projects/{kart}/datasets").json()["items"]}
        assert "RPT_KART_PANO" in names
        review = client.get(f"/api/projects/{kart}/review?type=bag").json()["items"]
        llm_items = [i for i in review if i["origin"] == "llm"]
        assert llm_items and llm_items[0]["title"].endswith("RPT_KART_PANO")
        assert llm.calls  # gerçekten istem gönderildi

    def test_confidence_threshold_drops_weak_llm_flows(self, client, kart, llm):
        llm.confidence = 0.5  # sistem en çok 0.8'e kırpar; 0.5 < 0.6 eşiği
        client.put(f"/api/projects/{kart}/settings", json={"patch": {"yz": {"guven_esigi": 0.6}}}, headers=H)
        job = client.post(f"/api/projects/{kart}/build", json={"llm": True}, headers=H).json()
        assert wait(client, job["id"])["state"] == "done"
        names = {d["name"] for d in client.get(f"/api/projects/{kart}/datasets").json()["items"]}
        assert "RPT_KART_PANO" not in names
        summary = client.get(f"/api/projects/{kart}").json()["summary"]
        assert summary["llm"]["below_threshold"] >= 1

    def test_external_llm_blocked(self, client, kart):
        client.put("/api/settings", json={"patch": {"yz": {"profil": "dis", "profiller": {
            "dis": {"base_url": "https://api.example.com/v1", "model": "x"}}}}}, headers=H)
        response = client.post(f"/api/projects/{kart}/build", json={"llm": True}, headers=H)
        assert response.status_code == 400 and "yerel" in response.json()["detail"]


class TestEndpoints:
    def test_profile_probe(self, client):
        result = client.post("/api/llm/test", json={"profile": "yerel"}, headers=H).json()
        assert result["ok"] and result["model_found"]

    def test_unknown_profile(self, client):
        assert client.post("/api/llm/test", json={"profile": "yok"}, headers=H).status_code == 400

    def test_body_cannot_carry_url(self, client, llm):
        """Regresyon (#14): istek gövdesiyle keyfi adrese istek attırılamaz."""
        client.post("/api/llm/test", json={"profile": "yerel", "base_url": "http://169.254.169.254/"}, headers=H)
        assert all("169.254" not in str(call) for call in llm.calls)

    def test_prompts_list_load_save(self, client):
        prompts = client.get("/api/llm/prompts").json()
        assert {p["id"] for p in prompts} >= {"extract_flows", "table_roles"}
        loaded = client.get("/api/llm/prompts/extract_flows").json()
        assert loaded["version"] >= 2 and "{text}" in loaded["user"]
        saved = client.post("/api/llm/prompts/extract_flows", json={"prompt": loaded}, headers=H).json()
        assert saved["version"] == loaded["version"] + 1
        assert client.get(f"/api/llm/prompts/extract_flows?version={saved['version']}").json()["origin"] == "user"
        bad = dict(loaded, user="{bilinmeyen}")
        assert client.post("/api/llm/prompts/extract_flows", json={"prompt": bad}, headers=H).status_code == 400

    def test_console(self, client):
        result = client.post("/api/llm/console", json={"text": TEXT}, headers=H).json()
        assert result["accepted"] and not result.get("error")
        dry = client.post("/api/llm/console", json={"text": TEXT, "dry_run": True}, headers=H).json()
        assert dry["sent"] and not dry.get("raw")

    def test_mask_preview(self, client):
        result = client.post("/api/llm/mask", json={"text": "IBAN TR33 0006 1005 1978 6457 8413 26, saat 01:00"},
                             headers=H).json()
        assert "[IBAN]" in result["masked"] and "01:00" in result["masked"]

    def test_roles(self, client, tmp_path):
        client.post("/api/users", json={"username": "izleyen", "role": "izleyici", "password": PASSWORD}, headers=H)
        app = client.app
        with TestClient(app, client=("10.0.0.2", 1)) as viewer:
            viewer.post("/api/auth/login", json={"username": "izleyen", "password": PASSWORD}, headers=H)
            assert viewer.post("/api/llm/console", json={"text": "x"}, headers=H).status_code == 403
            assert viewer.post("/api/llm/prompts/extract_flows", json={"prompt": {}}, headers=H).status_code == 403
            assert viewer.get("/api/llm/prompts").status_code == 200


def test_llm_column_mappings_are_drafts():
    """Regresyon: LLM'in kolon eşlemeleri onaylı giriyordu (column_flow taslak desteklemiyordu)."""
    from dwhflow.daf import ExtractDoc, apply_extract
    from dwhflow.ingest.base import ModelBuilder

    builder = ModelBuilder()
    doc = ExtractDoc.model_validate({"flows": [{"source": "STG_A", "target": "ODS_A", "evidence": "e",
                                                "columns": [{"source": "X", "target": "Y"}]}]})
    counts = apply_extract(builder, doc, file="f", locator="l", extractor="llm:test")
    assert counts["columns"] == 1 and counts["columns_skipped"] == 0
    edge = next(e for e in builder.build().edges if e.kind is EdgeKind.COLUMN_MAPS_TO)
    assert edge.status is Status.DRAFT
    # Aynı eşleme yapısal bir kaynaktan da gelirse onaylı olur.
    builder.column_flow("STG_A", "X", "ODS_A", "Y")
    edge = next(e for e in builder.build().edges if e.kind is EdgeKind.COLUMN_MAPS_TO)
    assert edge.status is Status.CONFIRMED

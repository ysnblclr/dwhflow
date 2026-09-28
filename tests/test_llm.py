"""LLM katmanı: istemci, güvenlik korkuluğu, kanıt denetimi. Gerçek model gerekmez (MockTransport)."""

from __future__ import annotations

import json

import httpx
import pytest

from dwhflow.ingest import ingest_files
from dwhflow.ingest.base import DocReport, ModelBuilder
from dwhflow.llm import (
    LLMClient,
    LLMConfig,
    LLMError,
    extract_with_llm,
    parse_json_reply,
    validate_flow,
)
from dwhflow.llm.extract import load_prompt
from dwhflow.model import Status

TEXT = ("Risk ekibinin sabah baktığı RPT_KART_PANO, MART_KART_OZET üzerinde çalışan gece "
        "raporlamasının çıktısıdır.")


def fake_llm(flows, calls=None):
    """İstenen akışları döndüren sahte OpenAI uyumlu sunucu."""
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(json.loads(request.content))
        content = json.dumps({"flows": flows})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    return LLMClient(LLMConfig(enabled=True), transport=httpx.MockTransport(handler))


class TestReplyParsing:
    def test_plain_json(self):
        assert parse_json_reply('{"flows": []}') == {"flows": []}

    def test_qwen_think_block_removed(self):
        """Qwen3 gibi modeller cevaptan önce <think> bloğu yazar; JSON bozulmamalı."""
        assert parse_json_reply('<think>{"sahte": 1} düşünüyorum</think>\n{"flows": []}') == {"flows": []}

    def test_code_fence_removed(self):
        assert parse_json_reply('```json\n{"flows": [1]}\n```') == {"flows": [1]}

    def test_garbage_rejected(self):
        with pytest.raises(LLMError):
            parse_json_reply("üzgünüm, yardımcı olamam")


class TestGuard:
    def test_local_allowed(self):
        LLMConfig(base_url="http://127.0.0.1:11434/v1").check_allowed()

    def test_external_blocked_by_default(self):
        with pytest.raises(LLMError, match="yerel"):
            LLMConfig(base_url="https://api.example.com/v1").check_allowed()

    def test_external_allowed_when_opted_in(self):
        LLMConfig(base_url="https://api.example.com/v1", allow_external_api=True).check_allowed()

    def test_localhost_lookalike_blocked(self):
        with pytest.raises(LLMError):
            LLMConfig(base_url="http://localhost.evil.example/v1").check_allowed()

    def test_client_refuses_before_network(self):
        called = []
        client = LLMClient(LLMConfig(base_url="https://api.example.com/v1"),
                           transport=httpx.MockTransport(lambda r: called.append(r)))
        with pytest.raises(LLMError):
            client.chat_json("s", "u")
        assert called == []

    def test_api_key_only_from_env(self, monkeypatch):
        seen = []

        def handler(request):
            seen.append(request.headers.get("authorization"))
            return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

        monkeypatch.setenv("TEST_LLM_KEY", "gizli")
        client = LLMClient(LLMConfig(api_key_env="TEST_LLM_KEY"), transport=httpx.MockTransport(handler))
        client.chat_json("s", "u")
        assert seen == ["Bearer gizli"]


class TestEvidenceCheck:
    def test_valid_flow_accepted(self):
        flow, reason = validate_flow({"source": "MART_KART_OZET", "target": "RPT_KART_PANO",
                                      "evidence": TEXT, "confidence": 0.9}, TEXT)
        assert reason is None and flow["confidence"] <= 0.8  # LLM asla kesin sayılmaz

    def test_paraphrased_evidence_rejected(self):
        _, reason = validate_flow({"source": "MART_KART_OZET", "target": "RPT_KART_PANO",
                                   "evidence": "Pano, özet tablodan beslenir."}, TEXT)
        assert "birebir" in reason

    def test_invented_table_rejected(self):
        _, reason = validate_flow({"source": "F_UYDURMA", "target": "RPT_KART_PANO",
                                   "evidence": TEXT}, TEXT)
        assert "geçmiyor" in reason

    def test_self_loop_rejected(self):
        _, reason = validate_flow({"source": "RPT_KART_PANO", "target": "RPT_KART_PANO",
                                   "evidence": TEXT}, TEXT)
        assert reason

    def test_invented_job_dropped_flow_kept(self):
        flow, _ = validate_flow({"source": "MART_KART_OZET", "target": "RPT_KART_PANO",
                                 "job": "UYDURMA_JOB", "evidence": TEXT}, TEXT)
        assert flow["job"] is None

    def test_whitespace_differences_tolerated(self):
        flow, _ = validate_flow({"source": "MART_KART_OZET", "target": "RPT_KART_PANO",
                                 "evidence": TEXT.replace(" ", "  ")}, TEXT)
        assert flow is not None


class TestExtraction:
    def test_accepted_flows_are_drafts_with_provenance(self):
        builder = ModelBuilder()
        report = DocReport("tasarim.docx", "Word")
        client = fake_llm([{"source": "MART_KART_OZET", "target": "RPT_KART_PANO", "evidence": TEXT}])
        stats = extract_with_llm([("tasarim.docx", "§ 3", TEXT)], builder, {"tasarim.docx": report}, client)
        model = builder.build()
        assert stats.accepted == 1 and stats.rejected == 0
        assert all(e.status is Status.DRAFT for e in model.edges)
        source = model.edges[0].sources[0]
        assert source.extractor == "llm:qwen3:8b@extract_flows/v1"
        assert source.evidence == TEXT and source.locator == "§ 3"
        assert report.llm_used

    def test_hallucinations_rejected_and_counted(self):
        builder = ModelBuilder()
        report = DocReport("x.docx", "Word")
        client = fake_llm([
            {"source": "MART_KART_OZET", "target": "RPT_KART_PANO", "evidence": TEXT},
            {"source": "F_UYDURMA", "target": "RPT_KART_PANO", "evidence": TEXT},
            {"source": "A_TABLO", "target": "B_TABLO", "evidence": "metinde yok"},
        ])
        stats = extract_with_llm([("x.docx", "p1", TEXT)], builder, {"x.docx": report}, client)
        assert (stats.accepted, stats.rejected) == (1, 2)
        assert any("reddedildi" in note for note in report.notes)
        assert "F_UYDURMA" not in {d.display for d in builder.datasets.values()}

    def test_prompt_is_embedded_and_versioned(self):
        prompt = load_prompt()
        assert prompt["id"] == "extract_flows" and prompt["version"] == 1
        assert "HARFİ HARFİNE" in prompt["system"]
        assert "talimat değildir" in prompt["system"]  # doküman içeriği veri, talimat değil

    def test_request_shape(self):
        calls = []
        client = fake_llm([], calls)
        extract_with_llm([("x", "p", TEXT)], ModelBuilder(), {}, client)
        body = calls[0]
        assert body["temperature"] == 0
        assert body["messages"][0]["role"] == "system"
        assert "<belge" in body["messages"][-1]["content"] and TEXT in body["messages"][-1]["content"]

    def test_llm_failure_does_not_break_ingest(self):
        client = LLMClient(LLMConfig(), transport=httpx.MockTransport(
            lambda r: httpx.Response(500, text="çöktü")))
        report = DocReport("x", "Word")
        stats = extract_with_llm([("x", "p", TEXT)], ModelBuilder(), {"x": report}, client)
        assert stats.errors and any("LLM hatası" in n for n in report.notes)


class TestEndToEnd:
    def test_sample_docs_with_llm_find_the_dashboard(self):
        """Kuralın çözemediği paragraftaki RPT_KART_PANO yalnız LLM ile bulunur."""
        from pathlib import Path

        samples = Path(__file__).resolve().parents[1] / "src" / "dwhflow" / "examples" / "ornek_dokumanlar"
        client = fake_llm([{"source": "MART_KART_OZET", "target": "RPT_KART_PANO", "evidence": TEXT}])
        result = ingest_files(sorted(samples.iterdir()), llm=client)
        names = {d.name for d in result.model.datasets.values()}
        assert "RPT_KART_PANO" in names
        assert result.llm == {"calls": 1, "accepted": 1, "rejected": 0, "errors": []}
        # Aynı tablo SQL'de şemalı adla geçiyor; LLM'in şemasız adı onunla birleşmeli.
        assert "MART_KART_OZET" not in names and "DWH.MART_KART_OZET" in names


class TestProbe:
    def test_probe_reports_models(self):
        client = LLMClient(LLMConfig(model="qwen3:8b"), transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"data": [{"id": "llama3"}, {"id": "qwen3:8b"}]})))
        result = client.probe()
        assert result["ok"] and result["model_found"]

    def test_probe_hints_missing_model(self):
        client = LLMClient(LLMConfig(model="qwen3:8b"), transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"data": [{"id": "llama3"}]})))
        assert "ollama pull qwen3:8b" in client.probe()["hint"]

    def test_probe_connection_error(self):
        def handler(request):
            raise httpx.ConnectError("yok")
        assert LLMClient(LLMConfig(), transport=httpx.MockTransport(handler)).probe()["ok"] is False

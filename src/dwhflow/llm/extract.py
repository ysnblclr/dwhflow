"""LLM ile serbest metinden akış çıkarma — kanıt denetimli.

Kuralların çözemediği paragraflar (iki tablo adı geçen ama yönü kalıptan
anlaşılamayan) buraya gelir. Modelin döndürdüğü her akış deterministik olarak
denetlenir; model "ikna edici" olsa bile:

* kanıt cümlesi kaynak metinde **harfi harfine** geçmiyorsa,
* kaynak ya da hedef adı metinde geçmiyorsa,
* kaynak ile hedef aynıysa

akış **reddedilir** ve sayısı raporlanır. Kabul edilenler taslak girer ve
``llm:<model>@extract_flows/v1`` kaynağıyla işaretlenir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..ingest.base import DocReport, ModelBuilder, ref
from ..model import ProcessKind
from .client import LLMClient, LLMError

PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "extract_flows.v1.yaml"
MAX_CONFIDENCE = 0.8  # LLM çıkarımı hiçbir zaman kesin sayılmaz


def load_prompt(path: Path = PROMPT_FILE) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().casefold()


@dataclass
class LLMStats:
    calls: int = 0
    accepted: int = 0
    rejected: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"calls": self.calls, "accepted": self.accepted, "rejected": self.rejected,
                "errors": self.errors[:10]}


def _chunks(paragraphs: list[tuple[str, str]], limit: int) -> list[tuple[str, str]]:
    """Paragrafları çağrı başına ``limit`` karakteri aşmayacak şekilde birleştirir."""
    out: list[tuple[str, str]] = []
    locators: list[str] = []
    buffer: list[str] = []
    size = 0
    for locator, text in paragraphs:
        text = text[:limit]
        if buffer and size + len(text) > limit:
            out.append((" / ".join(locators[:3]), "\n\n".join(buffer)))
            locators, buffer, size = [], [], 0
        locators.append(locator)
        buffer.append(text)
        size += len(text) + 2
    if buffer:
        out.append((" / ".join(locators[:3]), "\n\n".join(buffer)))
    return out


def validate_flow(item: Any, chunk: str) -> tuple[dict[str, Any] | None, str | None]:
    """Tek bir LLM akışını denetler. (kabul edilen, red nedeni)."""
    if not isinstance(item, dict):
        return None, "akış nesne değil"
    source = str(item.get("source") or "").strip()
    target = str(item.get("target") or "").strip()
    evidence = str(item.get("evidence") or "").strip()
    if not source or not target:
        return None, "kaynak ya da hedef boş"
    if source.casefold() == target.casefold():
        return None, "kaynak ile hedef aynı"
    haystack = _norm(chunk)
    if not evidence or _norm(evidence) not in haystack:
        return None, "kanıt cümlesi metinde birebir geçmiyor"
    for name in (source, target):
        if _norm(name) not in haystack:
            return None, f"'{name}' metinde geçmiyor"
    job = str(item.get("job") or "").strip() or None
    if job and _norm(job) not in haystack:
        job = None  # uydurma job adı akışı düşürmez, yalnız job'ı atar
    try:
        confidence = float(item.get("confidence", 0.6))
    except (TypeError, ValueError):
        confidence = 0.6
    return {
        "source": source, "target": target, "job": job, "evidence": evidence,
        "confidence": max(0.1, min(MAX_CONFIDENCE, confidence)),
    }, None


def extract_with_llm(
    candidates: list[tuple[str, str, str]],
    builder: ModelBuilder,
    reports: dict[str, DocReport],
    client: LLMClient,
) -> LLMStats:
    """``candidates`` = [(dosya, konum, paragraf)]. Sonuçlar builder'a taslak olarak eklenir."""
    prompt = load_prompt()
    extractor = f"llm:{client.config.model}@{prompt['id']}/v{prompt['version']}"
    examples = [(shot["user"], shot["assistant"]) for shot in prompt.get("fewshot", [])]
    stats = LLMStats()

    by_file: dict[str, list[tuple[str, str]]] = {}
    for file, locator, text in candidates:
        by_file.setdefault(file, []).append((locator, text))

    for file, paragraphs in by_file.items():
        report = reports.get(file)
        rejected_before = stats.rejected
        for locator, chunk in _chunks(paragraphs, client.config.max_chars_per_call):
            user = prompt["user"].format(file=file, locator=locator, text=chunk)
            stats.calls += 1
            try:
                reply = client.chat_json(prompt["system"], user, examples)
            except LLMError as exc:
                stats.errors.append(str(exc))
                if report:
                    report.note(f"LLM hatası: {str(exc)[:160]}")
                continue
            flows = reply.get("flows")
            if not isinstance(flows, list):
                stats.rejected += 1
                continue
            for item in flows:
                accepted, reason = validate_flow(item, chunk)
                if not accepted:
                    stats.rejected += 1
                    continue
                source = ref(file, locator, extractor, confidence=accepted["confidence"],
                             evidence=accepted["evidence"])
                via = (builder.process(accepted["job"], source, draft=True, kind=ProcessKind.ETL_MAPPING)
                       if accepted["job"] else None)
                a = builder.dataset(accepted["source"], source, draft=True)
                b = builder.dataset(accepted["target"], source, draft=True)
                if builder.flow(a, b, via=via, source=source, draft=True):
                    stats.accepted += 1
                    if report:
                        report.flows += 1
                        report.llm_used = True
        rejected_here = stats.rejected - rejected_before
        if report and rejected_here:
            report.note(f"LLM önerilerinden {rejected_here} tanesi kanıt denetiminden geçemedi ve reddedildi")
    return stats

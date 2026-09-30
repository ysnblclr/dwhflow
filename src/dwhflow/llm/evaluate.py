"""Model ve istem kalitesini ölçme (US-6.5).

Altın set bir JSONL dosyasıdır; her satır bir paragraf ve ondan beklenen akışlardır::

    {"id": "k01", "text": "…", "flows": [{"source": "STG_A", "target": "ODS_A"}], "not": "…"}

İsteğe bağlı alanlar: ``file``, ``locator``, ``known_names`` (modele verilecek bilinen adlar).

Ölçü **tablo düzeyinde akıştır**: (kaynak, hedef) çifti; süreç ve kolon eşlemesi sayılmaz,
adlar büyük/küçük harf ve şema önekine duyarsız karşılaştırılır (``DWH.X`` = ``X``).
Tahmin, üretimdeki hattın aynısıyla elde edilir: maskeleme → LLM → şema ve dayanak
denetimi; reddedilen öğeler tahmin sayılmaz. Kesinlik/duyarlılık/F1 bütün paragraflar
üzerinden toplanarak (mikro ortalama) hesaplanır.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..ingest.base import bare_key, name_key
from . import prompts as prompt_library
from .client import LLMClient
from .extract import DEFAULT_PROMPT, ask_chunk, make_chunks, relevant_names
from .mask import should_mask

DEFAULT_DATASET = Path(__file__).resolve().parent / "eval" / "kart_paragraflar.jsonl"


def _flow_key(name: Any) -> str:
    return bare_key(name_key(str(name)))


def _label(pair: tuple[str, str]) -> str:
    return f"{pair[0]} → {pair[1]}"


def load_golden(path: Path | str | None = None) -> list[dict[str, Any]]:
    """Altın seti okur ve doğrular (ValueError: satır numarasıyla)."""
    path = Path(path) if path else DEFAULT_DATASET
    cases: list[dict[str, Any]] = []
    ids: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        where = f"{path.name}:{number}"
        try:
            data = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"{where}: geçerli JSON değil ({exc})") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{where}: satır bir JSON nesnesi olmalı")
        case_id, text, flows = data.get("id"), data.get("text"), data.get("flows")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"{where}: 'id' boş olmayan bir metin olmalı")
        if case_id in ids:
            raise ValueError(f"{where}: '{case_id}' kimliği tekrar ediyor")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{where}: 'text' boş olmayan bir metin olmalı")
        if not isinstance(flows, list) or not all(
                isinstance(f, dict) and isinstance(f.get("source"), str) and f["source"].strip()
                and isinstance(f.get("target"), str) and f["target"].strip() for f in flows):
            raise ValueError(f"{where}: 'flows' [{{source, target}}] listesi olmalı")
        known = data.get("known_names") or []
        if not isinstance(known, list):
            raise ValueError(f"{where}: 'known_names' bir liste olmalı")
        ids.add(case_id)
        cases.append({
            "id": case_id, "text": text,
            "flows": [{"source": f["source"].strip(), "target": f["target"].strip()} for f in flows],
            "note": data.get("not") or data.get("note"), "file": str(data.get("file") or path.name),
            "locator": str(data.get("locator") or case_id), "known_names": [str(n) for n in known],
        })
    return cases


def _ratio(numerator: int, denominator: int, empty: float) -> float:
    return round(numerator / denominator, 4) if denominator else empty


def evaluate(client: LLMClient, dataset_path: Path | str | None = None, *, prompt_id: str = DEFAULT_PROMPT,
             version: int | None = None, user_prompt_dir: Path | None = None,
             mask: bool | None = None) -> dict[str, Any]:
    """Altın set üzerinde kesinlik/duyarlılık/F1 hesaplar.

    LLM hataları (HTTP, bozuk JSON) paragraf başına ``errors``'ta sayılır ve o paragrafın
    tahmini boş kabul edilir. Dış adrese izin yoksa ``LLMError``, istem yoksa ``PromptError``
    fırlatılır (ölçüm hiç başlamaz).
    """
    cases = load_golden(dataset_path)
    if prompt_id != DEFAULT_PROMPT:
        raise prompt_library.PromptError(f"değerlendirme yalnız '{DEFAULT_PROMPT}' istemiyle yapılır")
    prompt = prompt_library.load_prompt(prompt_id, version, user_prompt_dir)
    client.config.check_allowed()
    masking = should_mask(client.config, mask)
    started = time.perf_counter()
    totals = {"tp": 0, "fp": 0, "fn": 0, "rejected": 0, "unresolved": 0, "errors": 0, "calls": 0}
    details = []
    for case in cases:
        predicted: set[tuple[str, str]] = set()
        shown: dict[tuple[str, str], tuple[str, str]] = {}
        errors: list[str] = []
        rejected = unresolved = 0
        case_started = time.perf_counter()
        for chunk in make_chunks(case["file"], [(case["locator"], case["text"])],
                                 client.config.max_chars_per_call, mask=masking):
            result = ask_chunk(client, prompt, chunk, known_names=relevant_names(case["known_names"], chunk.text),
                               doc_type="metin")
            totals["calls"] += 1
            if result.error is not None or result.review is None:
                errors.append(result.error or "yanıt işlenemedi")
                continue
            rejected += len(result.review.rejected)
            unresolved += len(result.review.unresolved)
            for accepted in result.review.accepted:
                if accepted.type == "flow":
                    pair = (_flow_key(accepted.item.source), _flow_key(accepted.item.target))
                    predicted.add(pair)
                    shown.setdefault(pair, (accepted.item.source, accepted.item.target))
        expected = {(_flow_key(f["source"]), _flow_key(f["target"])): (f["source"], f["target"])
                    for f in case["flows"]}
        hits = predicted & set(expected)
        false_positives = sorted(_label(shown[pair]) for pair in predicted - set(expected))
        missed = sorted(_label(expected[pair]) for pair in set(expected) - predicted)
        totals["tp"] += len(hits)
        totals["fp"] += len(false_positives)
        totals["fn"] += len(missed)
        totals["rejected"] += rejected
        totals["unresolved"] += unresolved
        totals["errors"] += len(errors)
        details.append({
            "id": case["id"], "note": case["note"],
            "expected": sorted(_label(pair) for pair in expected.values()),
            "predicted": sorted(_label(shown[pair]) for pair in predicted),
            "tp": len(hits), "fp": len(false_positives), "fn": len(missed),
            "false_positives": false_positives, "missed": missed,
            "rejected": rejected, "unresolved": unresolved, "errors": errors,
            "duration_ms": int((time.perf_counter() - case_started) * 1000),
        })
    tp, fp, fn = totals["tp"], totals["fp"], totals["fn"]
    nothing = 1.0 if tp + fp + fn == 0 else 0.0  # hiç beklenen ve tahmin yoksa kusursuz sayılır
    precision = _ratio(tp, tp + fp, nothing)
    recall = _ratio(tp, tp + fn, nothing)
    f1 = round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0
    return {
        "model": client.config.model, "base_url": client.config.base_url,
        "prompt": {"id": prompt["id"], "version": prompt["version"], "origin": prompt["origin"]},
        "dataset": str(Path(dataset_path).name if dataset_path else DEFAULT_DATASET.name),
        "cases": len(cases), "masking": masking, **totals,
        "precision": precision, "recall": recall, "f1": f1,
        "duration_ms": int((time.perf_counter() - started) * 1000), "details": details,
    }

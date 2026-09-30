"""İstem test konsolu (US-6.4): bir paragrafı yapıştır, LLM sonucunu anında gör.

Tüm projeyi yeniden okutmadan istem denemek içindir; **hiçbir modele dokunmaz**. Çıktı
JSON'a çevrilebilir bir sözlüktür: LLM'e giden mesaj (maskeli), ham yanıt, ayrıştırılmış
yanıt, kabul/ret (nedenleriyle), belirsiz cümleler, DAF görünümü ve süre.

``dry_run=True`` LLM'i çağırmaz; yalnız gönderilecek mesajı ve maskeleme sonucunu gösterir
(US-6.8 gönderim önizlemesi).
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from . import prompts as prompt_library
from .client import LLMClient
from .extract import MAX_CONFIDENCE, ask_chunk, build_user_message, extract_doc, make_chunks, relevant_names
from .mask import should_mask
from .table_roles import _suggest


def parse_header_text(text: str) -> tuple[list[str], list[list[str]]]:
    """Yapıştırılan başlık satırını (ve ≤3 örnek satırı) hücrelere ayırır.

    Ayırıcı ilk satırdan seçilir: ``|`` (Markdown tablosu dahil), sekme, ``;`` ya da ``,``.
    Markdown ayırıcı satırı (``|---|---|``) atlanır.
    """
    lines = [line for line in (text or "").splitlines() if line.strip()]
    lines = [line for line in lines if not re.fullmatch(r"[\s|:+-]+", line)]
    if not lines:
        return [], []
    separator = next((sep for sep in ("|", "\t", ";") if sep in lines[0]), ",")

    def cells(line: str) -> list[str]:
        line = line.strip()
        if separator == "|":
            line = line.strip("|")
        return [cell.strip() for cell in line.split(separator)]

    return cells(lines[0]), [cells(line) for line in lines[1:4]]


def _masking(client: LLMClient, mask: bool | None) -> dict[str, Any]:
    return {"applied": should_mask(client.config, mask), "forced": not client.config.is_local, "counts": {}}


def run_console(client: LLMClient, text: str, *, prompt_id: str = "extract_flows", version: int | None = None,
                file: str = "konsol", locator: str = "konsol", user_dir: Path | None = None,
                known_names: list[str] | None = None, mask: bool | None = None, doc_type: str = "metin",
                dry_run: bool = False) -> dict[str, Any]:
    """Metni istemle LLM'e gönderir, sonucu denetleyip döner (model değişmez).

    ``prompt_id="table_roles"`` iken ``text`` bir başlık satırıdır (altında ≤3 örnek satır olabilir).
    Döner: task, prompt {id, version, origin}, sent, masked {applied, forced, counts}, raw, parsed,
    accepted, rejected [{item, reason, …}], dropped, unresolved, daf, error, duration_ms (+ göreve özgü alanlar).
    """
    if prompt_id == "table_roles":
        return _table_console(client, text, version=version, file=file, locator=locator, user_dir=user_dir,
                              mask=mask, dry_run=dry_run)
    result: dict[str, Any] = {
        "task": prompt_id, "prompt": None, "extractor": None, "sent": None, "chars": len(text or ""),
        "limit": client.config.max_chars_per_call, "warning": None, "masked": _masking(client, mask),
        "raw": None, "parsed": None, "accepted": [], "rejected": [], "dropped": [], "unresolved": [],
        "daf": None, "error": None, "duration_ms": 0,
    }
    started = time.perf_counter()
    try:
        prompt_library.task_of(prompt_id)
        prompt = prompt_library.load_prompt(prompt_id, version, user_dir)
    except prompt_library.PromptError as exc:
        result["error"] = str(exc)
        return result
    result["prompt"] = {"id": prompt["id"], "version": prompt["version"], "origin": prompt["origin"]}
    result["extractor"] = f"llm:{client.config.model}@{prompt['id']}/v{prompt['version']}"
    chunks = make_chunks(file, [(locator, text)], None, mask=result["masked"]["applied"])
    if not chunks:
        result["error"] = "metin boş"
        return result
    chunk = chunks[0]
    result["masked"]["counts"] = dict(chunk.masked)
    if len(chunk.text) > client.config.max_chars_per_call:
        result["warning"] = (f"metin parça sınırını ({client.config.max_chars_per_call} karakter) aşıyor; "
                             "toplu okumada cümle sınırından bölünerek gönderilir")
    names = relevant_names(known_names or [], chunk.text)
    if dry_run:
        result["sent"] = build_user_message(prompt, chunk, known_names=names, doc_type=doc_type)
        return result

    outcome = ask_chunk(client, prompt, chunk, known_names=names, doc_type=doc_type, confidence_cap=MAX_CONFIDENCE)
    result.update({"sent": outcome.sent, "raw": outcome.raw, "parsed": outcome.parsed, "error": outcome.error})
    if outcome.review is not None:
        review = outcome.review
        result["accepted"] = [item.as_dict() for item in review.accepted]
        result["rejected"] = [dict(entry) for entry in review.rejected]
        result["dropped"] = [dict(entry) for entry in review.dropped]
        result["unresolved"] = [dict(entry) for entry in review.unresolved]
        result["merged"] = review.merged
        result["daf"] = extract_doc(review.accepted).model_dump(mode="json", by_alias=True)
    result["duration_ms"] = int((time.perf_counter() - started) * 1000)
    return result


def _table_console(client: LLMClient, text: str, *, version: int | None, file: str, locator: str,
                   user_dir: Path | None, mask: bool | None, dry_run: bool) -> dict[str, Any]:
    headers, rows = parse_header_text(text)
    result: dict[str, Any] = {
        "task": "table_roles", "prompt": None, "sent": None, "masked": _masking(client, mask),
        "headers": headers, "rows": rows, "raw": None, "parsed": None, "accepted": [], "rejected": [],
        "dropped": [], "unresolved": [], "usable": False, "table_roles": None, "daf": None, "synonyms": {},
        "reasons": [], "error": None, "duration_ms": 0,
    }
    if not any(headers):
        result["error"] = "başlık satırı boş"
        return result
    started = time.perf_counter()
    suggestion = _suggest(client, headers, rows, file=file, locator=locator, prompt_version=version,
                          user_prompt_dir=user_dir, mask=mask, dry_run=dry_run)
    roles = suggestion.table_roles.model_dump(mode="json", by_alias=True) if suggestion.table_roles else None
    result.update({
        "prompt": dict(suggestion.prompt) if suggestion.prompt else None, "sent": suggestion.sent,
        "raw": suggestion.raw, "parsed": suggestion.parsed, "usable": suggestion.usable,
        "table_roles": roles, "daf": roles, "synonyms": suggestion.synonyms,
        "reasons": list(suggestion.reasons), "error": suggestion.error,
        "accepted": ([{"header": header, "role": role} for header, role in suggestion.table_roles.roles.items()]
                     if suggestion.table_roles else []),
        "rejected": [{"item": {"header": entry["header"], "role": entry["role"]}, "reason": entry["reason"]}
                     for entry in suggestion.rejected],
    })
    result["masked"]["counts"] = dict(suggestion.masked)
    result["duration_ms"] = int((time.perf_counter() - started) * 1000)
    return result

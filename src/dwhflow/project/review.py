"""Onay kuyruğu (PLAN_V2 US-3.6): modele kendiliğinden girmemesi gereken her şey.

Öğe türleri:

* ``bag``      — yalnız serbest metinden/LLM'den gelen (taslak) tablo düzeyi bağ
* ``nesne``    — hiçbir bağı olmayan taslak nesne
* ``birlestir``— aynı tablonun farklı yazımı olabilecek nesne çiftleri
* ``paragraf`` — tablo adı geçen ama akışı kuralla çıkarılamayan paragraf (insan bakmalı)
* ``tablo``    — başlıkları tanınmayan tablo (sütunlar elle ya da LLM önerisiyle eşlenir)

Her öğenin **kararlı** bir anahtarı vardır (yeniden okumada aynı kalır) ve kabul/ret
karşılığı düzeltme işlemleri (``accept_ops`` / ``reject_ops``) öğeyle birlikte verilir;
ret edilen öneri ``oneri_reddet`` ile saklanır ve bir daha sorulmaz.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from typing import Any

from ..ingest.base import bare_key, is_file_name, name_key
from ..ingest.config import ROLE_LABELS, active
from ..model import COLUMN_SEP, EdgeKind, Model, Status
from ..render.slices import dataset_links

MAX_ITEMS_PER_TYPE = 300
_AFFIXES = (re.compile(r"^(TBL|TABLE|TABLO)_"), re.compile(r"_(TBL|TABLE|TABLO|T)$"))


def _sources(items) -> list[dict[str, Any]]:
    out, seen = [], set()
    for source in items:
        key = (source.file, source.locator, source.evidence)
        if key in seen:
            continue
        seen.add(key)
        out.append({"file": source.file, "locator": source.locator, "evidence": source.evidence,
                    "extractor": source.extractor, "confidence": source.confidence})
    return out[:8]


def _ref_name(model: Model, ref: str) -> str:
    node_id, sep, column = ref.partition(COLUMN_SEP)
    node = model.node(node_id)
    name = node.name if node else node_id
    return f"{name}{sep}{column}"


def _edge_payload(model: Model, edge) -> dict[str, Any]:
    return {"kaynak": _ref_name(model, edge.source), "hedef": _ref_name(model, edge.target),
            "tur": edge.kind.value}


def draft_links(model: Model, rejected: set[str]) -> list[dict[str, Any]]:
    items = []
    for (source, target), link in sorted(dataset_links(model).items()):
        if not link.draft:
            continue
        s_node, t_node = model.datasets[source], model.datasets[target]
        key = f"bag:{name_key(s_node.name)}>{name_key(t_node.name)}"
        if key in rejected:
            continue
        edges = [e for e in model.edges if e.status is Status.DRAFT and (
            (e.source == source and e.target == target) or
            (e.source == source and e.target in link.processes and e.kind is EdgeKind.READS) or
            (e.target == target and e.source in link.processes and e.kind is EdgeKind.WRITES))]
        if not edges:
            continue
        sources = _sources(s for e in edges for s in e.sources)
        llm = bool(sources) and all(s["extractor"].startswith("llm:") for s in sources)
        accept = [{"islem": "bag_ekle", "hedef": _edge_payload(model, e)} for e in edges]
        accept += [{"islem": "onayla", "hedef": {"nesne": node.name}}
                   for node in (s_node, t_node) if node.status is Status.DRAFT]
        accept += [{"islem": "onayla", "hedef": {"nesne": model.processes[p].name}}
                   for p in link.processes if model.processes[p].status is Status.DRAFT]
        items.append({
            "key": key, "type": "bag", "origin": "llm" if llm else "metin",
            "title": f"{s_node.name} → {t_node.name}",
            "detail": ", ".join(model.processes[p].name for p in link.processes) or "doğrudan",
            "source": s_node.name, "target": t_node.name,
            "processes": [model.processes[p].name for p in link.processes],
            "edges": [_edge_payload(model, e) for e in edges], "evidence": sources,
            "confidence": max((s["confidence"] for s in sources), default=0.0),
            "accept_ops": accept,
            "reject_ops": [{"islem": "bag_sil", "hedef": _edge_payload(model, e)} for e in edges],
        })
        if len(items) >= MAX_ITEMS_PER_TYPE:
            break
    return items


def lonely_drafts(model: Model, rejected: set[str]) -> list[dict[str, Any]]:
    connected = {e.source_node for e in model.edges} | {e.target_node for e in model.edges}
    items = []
    for node in list(model.datasets.values()) + list(model.processes.values()):
        if node.status is not Status.DRAFT or node.id in connected:
            continue
        key = f"nesne:{name_key(node.name)}"
        if key in rejected:
            continue
        items.append({
            "key": key, "type": "nesne", "origin": "metin", "title": node.name,
            "detail": f"{node.node_type.value} · {node.layer.value}", "evidence": _sources(node.sources),
            "accept_ops": [{"islem": "onayla", "hedef": {"nesne": node.name}}],
            "reject_ops": [{"islem": "nesne_sil", "hedef": {"nesne": node.name}}],
        })
    return items[:MAX_ITEMS_PER_TYPE]


def _core(name: str) -> str:
    value = bare_key(name_key(name))
    for pattern in _AFFIXES:
        value = pattern.sub("", value)
    return value


def merge_suggestions(model: Model, rejected: set[str]) -> list[dict[str, Any]]:
    """Aynı tablonun farklı yazımı olabilecek çiftler. Temkinli: yalnız tek aday varsa önerir."""
    datasets = [d for d in model.datasets.values() if not is_file_name(name_key(d.name))]
    config = active()
    pairs: dict[tuple[str, str], str] = {}

    # 1) Aynı çıplak ad, farklı şema: DWH.X ~ EDW.X
    by_bare: dict[str, list] = {}
    for dataset in datasets:
        by_bare.setdefault(bare_key(name_key(dataset.name)), []).append(dataset)
    for group in by_bare.values():
        if len(group) == 2:
            pairs[tuple(sorted((group[0].id, group[1].id)))] = "aynı ad, farklı şema"

    # 2) Katman öneki olmayan ad, önekli tek bir adla aynı çekirdeğe sahip: MUSTERI_TBL ~ STG_MUSTERI
    prefixed: dict[str, list] = {}
    plain = []
    for dataset in datasets:
        bare = bare_key(name_key(dataset.name))
        rule = config.match_rule(bare)
        if rule is not None and rule.type == "onek":
            prefixed.setdefault(_core(bare[len(rule.value):]), []).append(dataset)
        else:
            plain.append(dataset)
    for dataset in plain:
        candidates = prefixed.get(_core(dataset.name), [])
        if len(candidates) > 1:
            # Birden çok aday (SRC_/STG_/ODS_X): yalnız aynı katmandaki tek aday önerilir.
            candidates = [c for c in candidates if c.layer == dataset.layer]
        if len(candidates) == 1:
            pairs.setdefault(tuple(sorted((dataset.id, candidates[0].id))), "katman öneki eksik yazım")

    # 3) Yazım hatası: çok benzer adlar (büyük modellerde maliyet için sınırlı)
    if len(datasets) <= 2000:
        buckets: dict[str, list] = {}
        for dataset in datasets:
            bare = bare_key(name_key(dataset.name))
            if len(bare) >= 8:
                buckets.setdefault(bare[:3], []).append((bare, dataset))
        for group in buckets.values():
            for index, (a_name, a) in enumerate(group):
                for b_name, b in group[index + 1:]:
                    if abs(len(a_name) - len(b_name)) > 2 or a.layer != b.layer:
                        continue
                    if difflib.SequenceMatcher(None, a_name, b_name).ratio() >= 0.92:
                        pairs.setdefault(tuple(sorted((a.id, b.id))), "çok benzer ad (yazım hatası olabilir)")

    items = []
    for (first, second), reason in sorted(pairs.items()):
        a, b = model.datasets[first], model.datasets[second]
        key = "birlestir:" + "|".join(sorted((name_key(a.name), name_key(b.name))))
        if key in rejected:
            continue
        # Tercih edilen ad: şemalı/önekli ya da daha çok kaynağı olan.
        preferred = max((a, b), key=lambda d: ("." in d.name, config.match_rule(bare_key(name_key(d.name)))
                                               is not None, len(d.sources)))
        items.append({
            "key": key, "type": "birlestir", "origin": "kural", "title": f"{a.name} ≈ {b.name}",
            "detail": reason, "names": [a.name, b.name], "preferred": preferred.name,
            "evidence": _sources([*a.sources[:2], *b.sources[:2]]),
            "accept_ops": [{"islem": "birlestir", "hedef": {"nesneler": [a.name, b.name]},
                            "deger": {"ad": preferred.name}}],
            "reject_ops": [{"islem": "oneri_reddet", "hedef": {"anahtar": key}}],
        })
    return items[:MAX_ITEMS_PER_TYPE]


def paragraph_key(file: str, locator: str, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return f"paragraf:{file}:{locator}:{digest}"


def paragraphs(leftovers: list, rejected: set[str]) -> list[dict[str, Any]]:
    items = []
    for entry in leftovers:
        file, locator, text = entry[0], entry[1], entry[2]
        reason = entry[3] if len(entry) > 3 else "tablo adı geçiyor ama akış yönü çıkarılamadı"
        key = paragraph_key(file, locator, text)
        if key in rejected:
            continue
        items.append({
            "key": key, "type": "paragraf", "origin": "metin", "title": f"{file} · {locator}",
            "detail": reason, "text": text[:2000], "file": file, "locator": locator,
            "accept_ops": [],  # kabul: kullanıcı bağı elle girer (arayüz formu açar)
            "reject_ops": [{"islem": "oneri_reddet", "hedef": {"anahtar": key}}],
        })
    return items[:MAX_ITEMS_PER_TYPE]


def unrecognized_tables(tables: list, suggestions: dict[str, dict], rejected: set[str]) -> list[dict[str, Any]]:
    items = []
    for file, locator, headers, rows in tables:
        key = f"tablo:{file}:{locator}"
        if key in rejected:
            continue
        suggestion = suggestions.get(key)
        items.append({
            "key": key, "type": "tablo", "origin": "llm" if suggestion else "kural",
            "title": f"{file} · {locator}", "detail": "başlıklar tanınmadı; sütunları rollere eşleyin",
            "file": file, "locator": locator, "headers": headers, "rows": rows,
            "roles": ROLE_LABELS, "suggestion": suggestion,
            "accept_ops": [],  # kabul: arayüz eşlemeyi ayar yaması olarak gönderir
            "reject_ops": [{"islem": "oneri_reddet", "hedef": {"anahtar": key}}],
        })
    return items[:MAX_ITEMS_PER_TYPE]


def compute(model: Model, ingest: dict[str, Any], rejected: set[str]) -> list[dict[str, Any]]:
    items = draft_links(model, rejected) + lonely_drafts(model, rejected) + merge_suggestions(model, rejected)
    llm = ingest.get("llm") or {}
    if llm:
        unresolved = [(u.get("file", ""), u.get("locator", ""), u.get("text", ""), u.get("reason") or
                       "LLM akışı çıkaramadı") for u in llm.get("unresolved", []) if u.get("text")]
        items += paragraphs(unresolved, rejected)
    else:
        items += paragraphs(ingest.get("leftovers", []), rejected)
    suggestions = {s["key"]: s for s in ingest.get("table_suggestions", []) if s.get("key")}
    items += unrecognized_tables(ingest.get("unrecognized_tables", []), suggestions, rejected)
    return items


def counts(items: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {"toplam": len(items)}
    for item in items:
        out[item["type"]] = out.get(item["type"], 0) + 1
    return out

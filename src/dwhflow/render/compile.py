"""Derleyici: model dilimi → Archify ``architecture`` spec'i.

Neden ``architecture`` tipi (``dataflow`` değil): spike ölçümleri.

* ``dataflow`` sabit geometri kullanıyor (aşama merkezleri x = 100 + i·215,
  ≤5 aşama, ≤5 satır, ilk aşamada node ≤152 px). Aynı katmanda birbirine akan
  tablolar ve uzun job adları sürekli çakışma üretti.
* ``architecture`` ızgarasında hücre ve boşluk ölçülerini biz veriyoruz.
  Gerçek bir DWH yolu (10 tablo, 9 job) showcase kalitesinde dört kapıyı da geçti.

Yerleşim: **katmanlar yukarıdan aşağıya satırlar**, aynı katmandaki nesneler yan
yana. Masaüstünde yazıların ≥6 px görünmesi için toplam genişlik ~1300 birimle
sınırlı; bir katmana sığmayan nesneler alt satıra kaydırılır. Dikey kaydırma
serbesttir.

Ölçüm sabitleri Archify'ın kendi tahminleriyle eşleşir (etiket ~6.6 px/karakter).
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from ..model import (
    LAYER_ORDER,
    DatasetKind,
    Layer,
    Model,
    ProcessKind,
    Sensitivity,
)
from .slices import Link, Slice, dataset_links

LOCALE_FILE = Path(__file__).resolve().parent / "locale_tr.json"

LABEL_PX = 6.6          # Archify architecture etiket tahmini (karakter başına)
SUBLABEL_PX = 5.0
EDGE_LABEL_PX = 6.0
NODE_H = 58
NODE_MIN_W = 120
NODE_MAX_W = 240
NODE_PAD = 24
GAP_Y = 96
GAP_X_MIN = 70
GAP_X_MAX = 260
ORIGIN = (60, 90)
WIDTH_BUDGET = 1300
LABEL_MAX_CHARS = int((NODE_MAX_W - NODE_PAD) / LABEL_PX)
EDGE_LABEL_MAX_CHARS = 30
CARD_ITEMS = 8

LAYER_TR: dict[Layer, str] = {
    Layer.SOURCE: "Kaynak",
    Layer.INTERFACE: "Arayüz / Landing",
    Layer.STAGING: "Staging",
    Layer.ODS: "ODS",
    Layer.CORE: "Çekirdek",
    Layer.MART: "Mart",
    Layer.CONSUMPTION: "Tüketim",
}

KIND_TYPE: dict[DatasetKind, str] = {
    DatasetKind.SOURCE_OBJECT: "external",
    DatasetKind.DOWNSTREAM_SYSTEM: "external",
    DatasetKind.INTERFACE_FILE: "messagebus",
    DatasetKind.LANDING_FILE: "messagebus",
    DatasetKind.EXPORT_FILE: "messagebus",
    DatasetKind.REPORT: "frontend",
    DatasetKind.CONTROL_TABLE: "security",
    DatasetKind.REJECT_TABLE: "security",
}

KIND_TR: dict[DatasetKind, str] = {
    DatasetKind.SOURCE_OBJECT: "kaynak nesne",
    DatasetKind.INTERFACE_FILE: "arayüz dosyası",
    DatasetKind.LANDING_FILE: "landing dosyası",
    DatasetKind.STAGING_TABLE: "staging tablosu",
    DatasetKind.ODS_TABLE: "ODS tablosu",
    DatasetKind.CORE_TABLE: "çekirdek tablo",
    DatasetKind.DIMENSION_TABLE: "boyut tablosu",
    DatasetKind.FACT_TABLE: "olgu tablosu",
    DatasetKind.AGGREGATE_TABLE: "özet tablo",
    DatasetKind.MART_TABLE: "mart tablosu",
    DatasetKind.EXPORT_FILE: "çıkış dosyası",
    DatasetKind.REPORT: "rapor",
    DatasetKind.DOWNSTREAM_SYSTEM: "dış sistem",
    DatasetKind.CONTROL_TABLE: "kontrol tablosu",
    DatasetKind.REJECT_TABLE: "red tablosu",
    DatasetKind.ARCHIVE_TABLE: "arşiv tablosu",
}

_DASHED_PROCESS = {ProcessKind.FILE_TRANSFER, ProcessKind.DECODER}


def translations() -> dict[str, str]:
    return json.loads(LOCALE_FILE.read_text(encoding="utf-8"))


def _ellipsize(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: max(1, limit - 1)].rstrip() + "…"


def _split_name(name: str) -> tuple[str, str | None]:
    """``"ACCT_DAILY (landing, EBCDIC)"`` → (``"ACCT_DAILY"``, ``"landing, EBCDIC"``)."""
    match = re.match(r"^(.*?)\s*\((.+)\)\s*$", name)
    return (match.group(1).strip(), match.group(2).strip()) if match else (name, None)


def archify_id(node_id: str) -> str:
    """Archify id'leri harfle başlamalı; bizde rakamla başlayan id olabiliyor."""
    return node_id if node_id[:1].isalpha() else f"n{node_id}"


def _node_texts(model: Model, dataset_id: str) -> tuple[str, str, str | None]:
    dataset = model.datasets[dataset_id]
    label, detail = _split_name(dataset.name)
    sublabel = detail or KIND_TR.get(dataset.kind, dataset.kind.value.replace("_", " "))
    tag = None
    if dataset.sensitivity is Sensitivity.PII or any(c.sensitivity is Sensitivity.PII for c in dataset.columns):
        tag = "PII"
    elif dataset.file_spec and (dataset.file_spec.encoding or "").lower().startswith("cp0"):
        tag = "EBCDIC"
    if dataset.status.value == "draft":
        tag = "TASLAK"
    return _ellipsize(label, LABEL_MAX_CHARS), _ellipsize(sublabel, 34), tag


def _edge_label(model: Model, link: Link) -> str:
    if link.processes:
        first = model.processes[link.processes[0]].name
        label = first if len(link.processes) == 1 else f"{first} +{len(link.processes) - 1}"
    elif "transfers" in link.direct_kinds:
        label = "aktarım"
    else:
        label = "türetim"
    if link.draft:
        label += " · taslak"
    return _ellipsize(label, EDGE_LABEL_MAX_CHARS)


def _variant(model: Model, link: Link, emphasized: set[tuple[str, str]]) -> str:
    if link.key in emphasized:
        return "emphasis"
    for dataset_id in link.key:
        dataset = model.datasets[dataset_id]
        if dataset.sensitivity is Sensitivity.PII:
            return "security"
    kinds = {model.processes[p].kind for p in link.processes}
    strategies = {model.processes[p].load_strategy for p in link.processes}
    if link.draft or kinds & _DASHED_PROCESS or any(s and s.value == "cdc" for s in strategies):
        return "dashed"
    return "default"


def _order_rows(layers: list[list[str]], links: list[Link]) -> list[list[str]]:
    """Barycenter süpürmesi: bağlı nesneleri alt alta getirerek çaprazlamayı azaltır."""
    position: dict[str, float] = {}
    for row in layers:
        for index, node in enumerate(row):
            position[node] = index
    preds: dict[str, list[str]] = {}
    succs: dict[str, list[str]] = {}
    for link in links:
        preds.setdefault(link.target, []).append(link.source)
        succs.setdefault(link.source, []).append(link.target)
    for sweep in range(4):
        rows = layers if sweep % 2 == 0 else list(reversed(layers))
        neighbors = preds if sweep % 2 == 0 else succs
        for row in rows:
            def center(node: str, neighbors=neighbors) -> float:
                values = [position[n] for n in neighbors.get(node, []) if n in position]
                return sum(values) / len(values) if values else position[node]
            row.sort(key=lambda n: (center(n), n))
            for index, node in enumerate(row):
                position[node] = index
    return layers


def compile_slice(
    model: Model,
    piece: Slice,
    *,
    output: str,
    quality: str = "showcase",
    findings: list[Any] | None = None,
    all_links: dict[tuple[str, str], Link] | None = None,
) -> dict[str, Any]:
    """Bir dilimi Archify spec'ine çevirir."""
    members = [d for d in piece.datasets if d in model.datasets]
    if len(members) < 1:
        raise ValueError("dilimde gösterilecek veri nesnesi yok")
    member_set = set(members)
    links_all = all_links if all_links is not None else dataset_links(model)
    links = [link for link in links_all.values()
             if link.source in member_set and link.target in member_set]

    # -- ölçüler ------------------------------------------------------------ #
    texts = {d: _node_texts(model, d) for d in members}
    widest = max(max(len(label) * LABEL_PX, len(sub) * SUBLABEL_PX) for label, sub, _ in texts.values())
    node_w = int(min(NODE_MAX_W, max(NODE_MIN_W, math.ceil(widest + NODE_PAD))))
    edge_labels = {link.key: _edge_label(model, link) for link in links}
    widest_edge = max((len(text) for text in edge_labels.values()), default=6) * EDGE_LABEL_PX + 30
    gap_x = int(min(GAP_X_MAX, max(GAP_X_MIN, math.ceil(widest_edge))))
    max_cols = max(1, min(12, (WIDTH_BUDGET - 2 * ORIGIN[0] + gap_x) // (node_w + gap_x)))

    # -- satırlar: katman sırası, sığmayan kaydırılır ----------------------- #
    by_layer: dict[Layer, list[str]] = {}
    for dataset_id in members:
        by_layer.setdefault(model.datasets[dataset_id].layer, []).append(dataset_id)
    layer_keys = sorted(by_layer, key=lambda layer: LAYER_ORDER[layer])
    ordered = _order_rows([sorted(by_layer[layer]) for layer in layer_keys], links)

    grid_rows: list[tuple[Layer, list[str]]] = []
    for layer, row in zip(layer_keys, ordered, strict=True):
        for start in range(0, len(row), max_cols):
            grid_rows.append((layer, row[start:start + max_cols]))
    cols = max(len(row) for _, row in grid_rows)

    emphasized = set(zip(piece.highlight, piece.highlight[1:], strict=False))

    components = []
    for row_index, (_, row) in enumerate(grid_rows):
        offset = (cols - len(row)) // 2  # kısa satırları ortala
        for col_index, dataset_id in enumerate(row):
            label, sublabel, tag = texts[dataset_id]
            component: dict[str, Any] = {
                "id": archify_id(dataset_id),
                "type": KIND_TYPE.get(model.datasets[dataset_id].kind, "database"),
                "label": label,
                "sublabel": sublabel,
                "col": offset + col_index,
                "row": row_index,
                "size": [node_w, NODE_H],
            }
            if tag:
                component["tag"] = tag
            components.append(component)

    connections = [
        {
            "id": f"{archify_id(link.source)}--{archify_id(link.target)}",
            "from": archify_id(link.source),
            "to": archify_id(link.target),
            "label": edge_labels[link.key],
            "variant": _variant(model, link, emphasized),
        }
        for link in sorted(links, key=lambda item: item.key)
    ]

    boundaries = [
        {
            "kind": "region",
            "label": LAYER_TR[layer],
            "wraps": [archify_id(d) for d in by_layer[layer]],
        }
        for layer in layer_keys
    ]

    spec: dict[str, Any] = {
        "schema_version": 1,
        "diagram_type": "architecture",
        "meta": {
            "title": _ellipsize(piece.title, 90),
            "subtitle": _ellipsize(piece.subtitle, 140) if piece.subtitle else None,
            "output": output,
            "quality_profile": quality,
            "locale": "tr",
            "translations": translations(),
        },
        "layout": {
            "mode": "grid",
            "cols": cols,
            "cellW": node_w,
            "cellH": NODE_H,
            "gapX": gap_x,
            "gapY": GAP_Y,
            "origin": list(ORIGIN),
        },
        "components": components,
        "boundaries": boundaries,
        "connections": connections,
        "cards": _cards(model, piece, members, links, findings or []),
    }
    if piece.kind == "path" and len(piece.highlight) > 1:
        spec["meta"]["animation"] = "trace"
    if not spec["meta"]["subtitle"]:
        del spec["meta"]["subtitle"]
    return spec


def _cards(model: Model, piece: Slice, members: list[str], links: list[Link],
           findings: list[Any]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    member_set = set(members)

    if piece.highlight:
        names = [model.datasets[d].name for d in piece.highlight]
        steps = [f"{i + 1}. {name}" for i, name in enumerate(names)]
        if len(steps) > CARD_ITEMS:
            steps = steps[:3] + [f"… {len(steps) - 5} adım …"] + steps[-2:]
        cards.append({"dot": "emerald", "title": "Vurgulanan yol", "items": steps})

    process_ids: list[str] = []
    for link in links:
        for process_id in link.processes:
            if process_id not in process_ids:
                process_ids.append(process_id)
    if process_ids:
        items = []
        for process_id in process_ids[:CARD_ITEMS]:
            process = model.processes[process_id]
            bits = [process.tool, process.load_strategy.value if process.load_strategy else None]
            detail = " · ".join(b for b in bits if b)
            items.append(f"{process.name}" + (f" — {detail}" if detail else ""))
        if len(process_ids) > CARD_ITEMS:
            items.append(f"… ve {len(process_ids) - CARD_ITEMS} süreç daha")
        cards.append({"dot": "cyan", "title": "Süreçler (kenar etiketleri)", "items": items})

    evidence: list[str] = []
    for dataset_id in members:
        for source in model.datasets[dataset_id].sources:
            evidence.append(_evidence_text(source))
    for edge in model.edges:
        if edge.source_node in member_set or edge.target_node in member_set:
            for source in edge.sources:
                evidence.append(_evidence_text(source))
    unique = list(dict.fromkeys(e for e in evidence if e))
    if unique:
        items = unique[:CARD_ITEMS]
        if len(unique) > CARD_ITEMS:
            items.append(f"… ve {len(unique) - CARD_ITEMS} kaynak daha")
        cards.append({"dot": "amber", "title": "Kaynak dokümanlar", "items": items})

    related = [f for f in findings
               if f.severity.value in _SEVERITY_TR and _subject_ids(f.subject) & member_set]
    related.sort(key=lambda f: f.severity.value != "error")
    if related:
        items = [f"{_SEVERITY_TR[f.severity.value]} · {_subject_text(model, f.subject)}: "
                 f"{_ellipsize(f.message, 90)}" for f in related[:6]]
        if len(related) > 6:
            items.append(f"… ve {len(related) - 6} bulgu daha")
        cards.append({"dot": "rose", "title": "Doğrulama bulguları", "items": items})
    return cards


_SEVERITY_TR = {"error": "Hata", "warning": "Uyarı"}
_SUBJECT_SPLIT = re.compile(r"[\s→>:#,]+")


def _subject_ids(subject: str | None) -> set[str]:
    """Bulgu öznesindeki node kimlikleri. Özne bir node (``stg_a``), kenar
    (``reads:stg_a>job``) ya da döngü (``a → b → a``) olabilir. Alt dize eşlemesi
    yapılmaz: ``stg_a`` ile ``stg_ab`` karışmasın."""
    return set(_SUBJECT_SPLIT.split(subject)) if subject else set()


def _subject_text(model: Model, subject: str | None) -> str:
    if not subject:
        return "model"
    node = model.node(subject)
    if node is not None:
        return node.name
    if ":" in subject and ">" in subject:  # kenar kimliği: tür:kaynak>hedef
        ends = subject.split(":", 1)[1].split(">", 1)
        names = [model.node(e.split("#", 1)[0]).name if model.node(e.split("#", 1)[0]) else e
                 for e in ends]
        return " → ".join(names)
    parts = [part.strip() for part in subject.split("→")]
    return " → ".join(model.node(part).name if model.node(part) else part for part in parts)


def _evidence_text(source: Any) -> str:
    if not source.file:
        return ""
    where = f" · {source.locator}" if source.locator else ""
    marker = " (YZ önerisi)" if source.is_llm else ""
    return _ellipsize(f"{Path(source.file).name}{where}{marker}", 90)


# --------------------------------------------------------------------------- #
# Batch zinciri: job'lar node, kenarlar yürütme sırası
# --------------------------------------------------------------------------- #

PROCESS_TYPE: dict[ProcessKind, str] = {
    ProcessKind.FILE_TRANSFER: "messagebus",
    ProcessKind.DECODER: "messagebus",
    ProcessKind.DQ_CHECK: "security",
    ProcessKind.RECONCILIATION: "security",
    ProcessKind.EXTRACT_JOB: "external",
    ProcessKind.MANUAL_STEP: "frontend",
}


def compile_batch(model: Model, piece: Slice, *, output: str, quality: str = "showcase",
                  findings: list[Any] | None = None) -> dict[str, Any]:
    """Job zincirini derler. Satırlar yürütme kademeleridir: aynı satırdakiler paralel koşabilir."""
    import networkx as nx

    from ..model import EdgeKind

    members = [p for p in piece.processes if p in model.processes]
    member_set = set(members)
    graph = nx.DiGraph()
    graph.add_nodes_from(members)
    for edge in model.edges:
        if edge.kind in (EdgeKind.PRECEDES, EdgeKind.TRIGGERS):
            if edge.source_node in member_set and edge.target_node in member_set:
                graph.add_edge(edge.source_node, edge.target_node)
    try:
        levels = [sorted(level) for level in nx.topological_generations(graph)]
    except nx.NetworkXUnfeasible:  # döngü: kademelendirilemez, tek satır dizisi olarak göster
        levels = [sorted(members)]

    def texts(process_id: str) -> tuple[str, str]:
        process = model.processes[process_id]
        bits = [process.tool or process.kind.value.replace("_", " ")]
        if process.schedule and process.schedule.start_times:
            bits.append(process.schedule.start_times[0])
        return _ellipsize(process.name, LABEL_MAX_CHARS), _ellipsize(" · ".join(bits), 34)

    labels = {p: texts(p) for p in members}
    widest = max(max(len(a) * LABEL_PX, len(b) * SUBLABEL_PX) for a, b in labels.values())
    node_w = int(min(NODE_MAX_W, max(NODE_MIN_W, math.ceil(widest + NODE_PAD))))
    gap_x = GAP_X_MIN
    max_cols = max(1, min(12, (WIDTH_BUDGET - 2 * ORIGIN[0] + gap_x) // (node_w + gap_x)))

    rows: list[list[str]] = []
    for level in levels:
        for start in range(0, len(level), max_cols):
            rows.append(level[start:start + max_cols])
    cols = max(len(row) for row in rows)
    components = []
    for row_index, row in enumerate(rows):
        offset = (cols - len(row)) // 2
        for col_index, process_id in enumerate(row):
            label, sublabel = labels[process_id]
            components.append({
                "id": archify_id(process_id),
                "type": PROCESS_TYPE.get(model.processes[process_id].kind, "backend"),
                "label": label, "sublabel": sublabel,
                "col": offset + col_index, "row": row_index, "size": [node_w, NODE_H],
            })
    connections = [
        {"id": f"{archify_id(a)}--{archify_id(b)}", "from": archify_id(a), "to": archify_id(b),
         "label": "sonra"}
        for a, b in sorted(graph.edges())
    ]
    starts = [labels[p][1] for p in members if model.processes[p].schedule]
    items = [f"{len(members)} job, {len(levels)} kademe",
             "Aynı satırdaki job'lar birbirini beklemez; paralel koşabilir.",
             "Kenar: öncül job başarıyla bittikten sonra ardılı başlar."]
    if starts:
        items.append("Zamanlanmış başlangıçlar: " + ", ".join(dict.fromkeys(starts))[:120])
    spec: dict[str, Any] = {
        "schema_version": 1,
        "diagram_type": "architecture",
        "meta": {"title": _ellipsize(piece.title, 90), "subtitle": _ellipsize(piece.subtitle, 140),
                 "output": output, "quality_profile": quality, "locale": "tr",
                 "translations": translations()},
        "layout": {"mode": "grid", "cols": cols, "cellW": node_w, "cellH": NODE_H,
                   "gapX": gap_x, "gapY": GAP_Y, "origin": list(ORIGIN)},
        "components": components,
        "connections": connections,
        "cards": [{"dot": "cyan", "title": "Batch zinciri", "items": items}],
    }
    return spec

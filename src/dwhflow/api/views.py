"""Model → arayüz JSON'u ve Türkçe etiketler."""

from __future__ import annotations

from typing import Any

from ..auth import ROLE_LABELS
from ..ingest.base import fold
from ..ingest.config import DEFAULT_ROLES, default_rules
from ..ingest.config import ROLE_LABELS as COLUMN_ROLE_LABELS
from ..model import COLUMN_SEP, DatasetKind, EdgeKind, Layer, LoadStrategy, Model, ProcessKind, Sensitivity
from ..project.corrections import OP_LABELS
from ..project.pipeline import ProjectState
from ..render.compile import KIND_TR, LAYER_TR
from ..render.slices import dataset_links

PROCESS_KIND_TR = {
    ProcessKind.EXTRACT_JOB: "extract job'ı", ProcessKind.BATCH_JOB: "batch job",
    ProcessKind.FILE_TRANSFER: "dosya aktarımı", ProcessKind.DECODER: "dönüştürücü (decoder)",
    ProcessKind.ETL_MAPPING: "ETL mapping", ProcessKind.LOADER_UTILITY: "yükleme aracı",
    ProcessKind.STORED_PROCEDURE: "saklı yordam", ProcessKind.SQL_SCRIPT: "SQL betiği",
    ProcessKind.SHELL_SCRIPT: "kabuk betiği", ProcessKind.DQ_CHECK: "veri kalite kontrolü",
    ProcessKind.RECONCILIATION: "mutabakat", ProcessKind.ARCHIVE_PURGE: "arşiv/temizlik",
    ProcessKind.MANUAL_STEP: "elle adım",
}
EDGE_KIND_TR = {
    EdgeKind.READS: "okur", EdgeKind.WRITES: "yazar", EdgeKind.PRODUCES_FILE: "dosya üretir",
    EdgeKind.CONSUMES_FILE: "dosya tüketir", EdgeKind.TRANSFERS: "aktarır", EdgeKind.PRECEDES: "önce çalışır",
    EdgeKind.TRIGGERS: "tetikler", EdgeKind.COLUMN_MAPS_TO: "kolon eşlemesi", EdgeKind.INFLUENCES: "etkiler",
    EdgeKind.DERIVES_FROM: "türetim", EdgeKind.VALIDATES: "doğrular", EdgeKind.RECONCILES: "mutabakat",
    EdgeKind.IMPLEMENTS: "uygular",
}
LOAD_TR = {
    LoadStrategy.FULL: "tam", LoadStrategy.DELTA: "delta", LoadStrategy.APPEND: "ekleme",
    LoadStrategy.MERGE: "merge", LoadStrategy.CDC: "CDC", LoadStrategy.SCD1: "SCD1", LoadStrategy.SCD2: "SCD2",
    LoadStrategy.TRUNCATE_LOAD: "boşalt-yükle", LoadStrategy.PARTITION_EXCHANGE: "bölüm değişimi",
}
SENSITIVITY_TR = {"public": "herkese açık", "internal": "kurum içi", "confidential": "gizli",
                  "pii": "kişisel veri", "restricted": "kısıtlı"}


def meta() -> dict[str, Any]:
    """Açılır listeler ve etiketler için tek kaynak."""
    return {
        "layers": [{"value": layer.value, "label": LAYER_TR[layer]} for layer in Layer],
        "dataset_kinds": [{"value": k.value, "label": KIND_TR.get(k, k.value)} for k in DatasetKind],
        "process_kinds": [{"value": k.value, "label": PROCESS_KIND_TR[k]} for k in ProcessKind],
        "edge_kinds": [{"value": k.value, "label": EDGE_KIND_TR[k]} for k in EdgeKind],
        "load_strategies": [{"value": k.value, "label": LOAD_TR[k]} for k in LoadStrategy],
        "sensitivity": [{"value": s.value, "label": SENSITIVITY_TR.get(s.value, s.value)} for s in Sensitivity],
        "column_roles": [{"value": role, "label": COLUMN_ROLE_LABELS[role], "builtin": list(syn)}
                         for role, syn in DEFAULT_ROLES.items()],
        "default_rules": [{"type": r.type, "value": r.value, "layer": r.layer.value,
                           "kind": r.kind.value if r.kind else None} for r in default_rules()],
        "operations": [{"value": k, "label": v} for k, v in OP_LABELS.items()],
        "user_roles": [{"value": k, "label": v} for k, v in ROLE_LABELS.items()],
    }


def _sources(items, limit: int = 12) -> list[dict[str, Any]]:
    return [{"file": s.file, "locator": s.locator, "extractor": s.extractor, "evidence": s.evidence,
             "confidence": s.confidence} for s in items[:limit]]


def ref_name(model: Model, ref: str) -> str:
    node_id, sep, column = ref.partition(COLUMN_SEP)
    node = model.node(node_id)
    return f"{node.name if node else node_id}{sep}{column}"


class ModelView:
    """Bir durum için hesaplanan indeksler (istek başına bir kez)."""

    def __init__(self, state: ProjectState) -> None:
        self.state = state
        self.model = state.model
        self.links = dataset_links(self.model)
        self.upstream: dict[str, list] = {}
        self.downstream: dict[str, list] = {}
        for (source, target), link in self.links.items():
            self.downstream.setdefault(source, []).append((target, link))
            self.upstream.setdefault(target, []).append((source, link))
        self.findings: dict[str, list] = {}
        for finding in state.findings:
            for node_id in _subject_ids(finding.subject):
                self.findings.setdefault(node_id, []).append(finding)

    def name(self, node_id: str) -> str:
        node = self.model.node(node_id)
        return node.name if node else node_id

    def finding_row(self, finding) -> dict[str, Any]:
        subject = finding.subject
        return {"rule": finding.rule, "severity": finding.severity.value, "subject": subject,
                "subject_name": subject_text(self.model, subject),
                "subject_type": self.model.node_type_of(subject) if subject else None,
                "message": finding.message}

    def dataset_row(self, dataset) -> dict[str, Any]:
        return {
            "id": dataset.id, "name": dataset.name, "kind": dataset.kind.value, "layer": dataset.layer.value,
            "system": dataset.system, "status": dataset.status.value, "sensitivity": dataset.sensitivity.value,
            "columns": len(dataset.columns), "sources": len(dataset.sources),
            "files": sorted({s.file for s in dataset.sources if s.file}),
            "manual": any(s.extractor.startswith("manual") for s in dataset.sources),
            "upstream": len(self.upstream.get(dataset.id, [])), "downstream": len(self.downstream.get(dataset.id, [])),
            "findings": len(self.findings.get(dataset.id, [])),
        }

    def process_row(self, process) -> dict[str, Any]:
        reads = [e.source_node for e in self.model.edges if e.target == process.id and e.kind is EdgeKind.READS]
        writes = [e.target_node for e in self.model.edges if e.source == process.id and e.kind is EdgeKind.WRITES]
        before = [e.source for e in self.model.edges if e.target == process.id and e.kind is EdgeKind.PRECEDES]
        after = [e.target for e in self.model.edges if e.source == process.id and e.kind is EdgeKind.PRECEDES]
        schedule = process.schedule
        return {
            "id": process.id, "name": process.name, "kind": process.kind.value, "layer": process.layer.value,
            "tool": process.tool, "system": process.system, "status": process.status.value,
            "load_strategy": process.load_strategy.value if process.load_strategy else None,
            "schedule": ", ".join(schedule.start_times) if schedule and schedule.start_times
            else (schedule.cron if schedule else None),
            "reads": len(reads), "writes": len(writes), "predecessors": len(before), "successors": len(after),
            "sources": len(process.sources), "files": sorted({s.file for s in process.sources if s.file}),
            "manual": any(s.extractor.startswith("manual") for s in process.sources),
            "findings": len(self.findings.get(process.id, [])),
        }

    def link_row(self, key, link) -> dict[str, Any]:
        source, target = key
        return {"source": source, "target": target, "source_name": self.name(source),
                "target_name": self.name(target), "draft": link.draft,
                "processes": [{"id": p, "name": self.name(p)} for p in link.processes],
                "kinds": sorted(str(getattr(k, "value", k)) for k in link.direct_kinds)}

    def neighbours(self, dataset_id: str) -> tuple[list[dict], list[dict]]:
        up = [{"id": s, "name": self.name(s), "draft": link.draft,
               "processes": [{"id": p, "name": self.name(p)} for p in link.processes]}
              for s, link in sorted(self.upstream.get(dataset_id, []))]
        down = [{"id": t, "name": self.name(t), "draft": link.draft,
                 "processes": [{"id": p, "name": self.name(p)} for p in link.processes]}
                for t, link in sorted(self.downstream.get(dataset_id, []))]
        return up, down

    def raw_edges(self, node_id: str) -> list[dict[str, Any]]:
        return [{"kind": e.kind.value, "source": ref_name(self.model, e.source),
                 "target": ref_name(self.model, e.target), "status": e.status.value,
                 "expression": e.expression, "sources": _sources(e.sources, 4)}
                for e in self.model.edges if node_id in (e.source_node, e.target_node)]

    def corrections_for(self, node) -> list[dict[str, Any]]:
        names = {fold(n) for n in [node.name, *node.attrs.get("eski_adlar", []),
                                   *node.attrs.get("birlesen_adlar", [])]}
        results = self.state.applied.by_id()
        out = []
        for op in self.state.corrections:
            values = [str(v) for v in op.hedef.values() if isinstance(v, str)]
            values += [str(v) for v in op.hedef.get("nesneler", []) or []]
            if any(fold(v.split(COLUMN_SEP)[0]) in names for v in values):
                row = op.model_dump(exclude_none=True)
                row.update(label=OP_LABELS[op.islem], result=results.get(op.id))
                out.append(row)
        return out


def _subject_ids(subject: str | None) -> set[str]:
    import re

    return set(re.split(r"[\s→>:#,]+", subject)) if subject else set()


def subject_text(model: Model, subject: str | None) -> str:
    if not subject:
        return "model"
    node = model.node(subject)
    if node is not None:
        return node.name
    if ":" in subject and ">" in subject:
        ends = subject.split(":", 1)[1].split(">", 1)
        return " → ".join(ref_name(model, end) for end in ends)
    return " → ".join(ref_name(model, part.strip()) for part in subject.split("→"))

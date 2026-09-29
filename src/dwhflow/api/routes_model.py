"""Model gezgini, STTM, etki/yol analizi, düzeltmeler ve değişiklik günlüğü."""

from __future__ import annotations

import io
import zipfile
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import Response

from ..graph import ModelGraph
from ..ingest.base import fold
from ..model import COLUMN_SEP, EdgeKind, Layer
from ..project import OP_LABELS, CorrectionError
from . import deps
from .routes_projects import _state
from .views import ModelView, _sources, ref_name

router = APIRouter()


def _page(items: list[dict[str, Any]], offset: int, limit: int) -> dict[str, Any]:
    limit = max(1, min(limit, 2000))
    return {"items": items[offset:offset + limit], "total": len(items), "offset": offset, "limit": limit}


def _matches(row: dict[str, Any], q: str, filters: dict[str, str | None]) -> bool:
    if q and fold(q) not in fold(row["name"]):
        return False
    return all(not value or str(row.get(key)) == value for key, value in filters.items())


@router.get("/api/projects/{slug}/datasets")
def datasets(request: Request, slug: str, q: str = "", layer: str | None = None, kind: str | None = None,
             system: str | None = None, status: str | None = None, offset: int = 0,
             limit: int = 500) -> dict[str, Any]:
    deps.user(request)
    view = ModelView(_state(request, deps.project(request, slug)))
    rows = [view.dataset_row(d) for d in view.model.datasets.values()]
    rows = [r for r in rows if _matches(r, q, {"layer": layer, "kind": kind, "system": system, "status": status})]
    rows.sort(key=lambda r: (_LAYER_INDEX[r["layer"]], r["name"]))
    return _page(rows, offset, limit)


_LAYER_INDEX = {layer.value: index for index, layer in enumerate(Layer)}


@router.get("/api/projects/{slug}/datasets/{node_id}")
def dataset_detail(request: Request, slug: str, node_id: str) -> dict[str, Any]:
    deps.user(request)
    view = ModelView(_state(request, deps.project(request, slug)))
    dataset = view.model.datasets.get(node_id)
    if dataset is None:
        raise HTTPException(404, "veri nesnesi bulunamadı")
    upstream, downstream = view.neighbours(node_id)
    column_in = [{"source": ref_name(view.model, e.source), "target": ref_name(view.model, e.target),
                  "expression": e.expression} for e in view.model.edges
                 if e.kind is EdgeKind.COLUMN_MAPS_TO and e.target_node == node_id]
    column_out = [{"source": ref_name(view.model, e.source), "target": ref_name(view.model, e.target),
                   "expression": e.expression} for e in view.model.edges
                  if e.kind is EdgeKind.COLUMN_MAPS_TO and e.source_node == node_id]
    return {
        **view.dataset_row(dataset), "description": dataset.description,
        "columns": [{"name": c.name, "data_type": c.data_type, "pk": c.pk} for c in dataset.columns],
        "upstream": upstream, "downstream": downstream, "column_in": column_in, "column_out": column_out,
        "sources": _sources(dataset.sources, 30), "edges": view.raw_edges(node_id),
        "findings": [view.finding_row(f) for f in view.findings.get(node_id, [])],
        "corrections": view.corrections_for(dataset),
        "aliases": [*dataset.attrs.get("eski_adlar", []), *dataset.attrs.get("birlesen_adlar", [])],
    }


@router.get("/api/projects/{slug}/processes")
def processes(request: Request, slug: str, q: str = "", kind: str | None = None, tool: str | None = None,
              status: str | None = None, offset: int = 0, limit: int = 500) -> dict[str, Any]:
    deps.user(request)
    view = ModelView(_state(request, deps.project(request, slug)))
    rows = [view.process_row(p) for p in view.model.processes.values()]
    rows = [r for r in rows if _matches(r, q, {"kind": kind, "tool": tool, "status": status})]
    rows.sort(key=lambda r: r["name"])
    return _page(rows, offset, limit)


@router.get("/api/projects/{slug}/processes/{node_id}")
def process_detail(request: Request, slug: str, node_id: str) -> dict[str, Any]:
    deps.user(request)
    view = ModelView(_state(request, deps.project(request, slug)))
    process = view.model.processes.get(node_id)
    if process is None:
        raise HTTPException(404, "süreç bulunamadı")

    def named(ids):
        return [{"id": i, "name": view.name(i)} for i in sorted(set(ids), key=view.name)]

    edges = view.model.edges
    radius = ModelGraph(view.model).blast_radius(node_id)
    return {
        **view.process_row(process), "description": process.description,
        "reads": named(e.source_node for e in edges if e.target == node_id and e.kind is EdgeKind.READS),
        "writes": named(e.target_node for e in edges if e.source == node_id and e.kind is EdgeKind.WRITES),
        "predecessors": named(e.source for e in edges if e.target == node_id and e.kind is EdgeKind.PRECEDES),
        "successors": named(e.target for e in edges if e.source == node_id and e.kind is EdgeKind.PRECEDES),
        "schedule_detail": process.schedule.model_dump(exclude_none=True) if process.schedule else None,
        "impact": {"processes": len(radius["processes"]), "datasets": len(radius["datasets"]),
                   "consumption": named(radius["consumption"])},
        "sources": _sources(process.sources, 30), "edges": view.raw_edges(node_id),
        "findings": [view.finding_row(f) for f in view.findings.get(node_id, [])],
        "corrections": view.corrections_for(process),
    }


@router.get("/api/projects/{slug}/links")
def links(request: Request, slug: str, draft: bool | None = None) -> list[dict[str, Any]]:
    deps.user(request)
    view = ModelView(_state(request, deps.project(request, slug)))
    rows = [view.link_row(key, link) for key, link in sorted(view.links.items())]
    return [r for r in rows if draft is None or r["draft"] == draft]


@router.get("/api/projects/{slug}/sttm")
def sttm(request: Request, slug: str, q: str = "") -> list[dict[str, Any]]:
    deps.user(request)
    model = _state(request, deps.project(request, slug)).model
    rows = []
    for edge in model.edges:
        if edge.kind is not EdgeKind.COLUMN_MAPS_TO:
            continue
        source_table, source_col = ref_name(model, edge.source).rsplit(COLUMN_SEP, 1)
        target_table, target_col = ref_name(model, edge.target).rsplit(COLUMN_SEP, 1)
        row = {"source_table": source_table, "source_column": source_col, "target_table": target_table,
               "target_column": target_col, "expression": edge.expression, "status": edge.status.value,
               "manual": any(s.extractor.startswith("manual") for s in edge.sources),
               "sources": _sources(edge.sources, 4)}
        text = " ".join(str(v) for v in row.values() if isinstance(v, str))
        if not q or fold(q) in fold(text):
            rows.append(row)
    rows.sort(key=lambda r: (r["target_table"], r["target_column"], r["source_table"]))
    return rows


@router.get("/api/projects/{slug}/impact/{node_id}")
def impact(request: Request, slug: str, node_id: str) -> dict[str, Any]:
    deps.user(request)
    view = ModelView(_state(request, deps.project(request, slug)))
    if view.model.node(node_id) is None:
        raise HTTPException(404, "nesne bulunamadı")
    radius = ModelGraph(view.model).blast_radius(node_id)
    return {key: [{"id": i, "name": view.name(i)} for i in sorted(values, key=view.name)]
            for key, values in radius.items()}


@router.get("/api/projects/{slug}/paths")
def paths(request: Request, slug: str, source: str, target: str, limit: int = 5) -> dict[str, Any]:
    deps.user(request)
    from ..render.slices import resolve

    view = ModelView(_state(request, deps.project(request, slug)))
    try:
        source_id, target_id = resolve(view.model, source), resolve(view.model, target)
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'")) from exc
    graph = ModelGraph(view.model)
    found = graph.paths(source_id, target_id, limit=max(1, min(limit, 20)))
    return {"source": source_id, "target": target_id,
            "paths": [[{"id": n, "name": view.name(n), "type": view.model.node_type_of(n)} for n in route]
                      for route in found]}


@router.get("/api/projects/{slug}/model/export")
def export_model(request: Request, slug: str, format: str = "json") -> Response:
    deps.user(request)
    project = deps.project(request, slug)
    state = _state(request, project)
    if format == "json":
        return Response(state.model.model_dump_json(indent=1), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{slug}-model.json"'})
    if format == "yaml":
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted((project.model_dir / "yaml").glob("*.yaml")):
                archive.write(path, f"{slug}-model/{path.name}")
        return Response(buffer.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{slug}-model.zip"'})
    raise HTTPException(400, "biçim json ya da yaml olmalı")


# -- düzeltmeler ------------------------------------------------------------ #


@router.get("/api/projects/{slug}/corrections")
def corrections(request: Request, slug: str) -> list[dict[str, Any]]:
    deps.user(request)
    state = _state(request, deps.project(request, slug))
    results = state.applied.by_id()
    return [{**op.model_dump(exclude_none=True), "label": OP_LABELS[op.islem], "result": results.get(op.id)}
            for op in state.corrections]


@router.post("/api/projects/{slug}/corrections")
def add_corrections(request: Request, slug: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    user = deps.editor(request)
    project = deps.project(request, slug)
    engine = deps.ctx(request).engine
    ops = body.get("ops") or []
    if not isinstance(ops, list):
        raise HTTPException(400, "ops bir liste olmalı")
    try:
        created = engine.add_corrections(project, ops, user=user["username"], reason=body.get("reason"))
    except CorrectionError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"created": created, "summary": engine.state(project).summary(project)}


@router.post("/api/projects/{slug}/corrections/preview")
def preview_corrections(request: Request, slug: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    user = deps.editor(request)
    project = deps.project(request, slug)
    try:
        return deps.ctx(request).engine.preview(project, body.get("ops") or [], user=user["username"])
    except (CorrectionError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/api/projects/{slug}/corrections/{correction_id}")
def undo(request: Request, slug: str, correction_id: str) -> dict[str, Any]:
    user = deps.editor(request)
    project = deps.project(request, slug)
    try:
        undone = deps.ctx(request).engine.undo(project, correction_id, user=user["username"])
    except CorrectionError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"undone": undone}


@router.post("/api/projects/{slug}/corrections/{correction_id}/redo")
def redo(request: Request, slug: str, correction_id: str) -> dict[str, Any]:
    user = deps.editor(request)
    project = deps.project(request, slug)
    try:
        return {"redone": deps.ctx(request).engine.redo(project, correction_id, user=user["username"])}
    except CorrectionError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/projects/{slug}/audit")
def audit(request: Request, slug: str, user: str | None = None, action: str | None = None,
          limit: int = 300) -> list[dict[str, Any]]:
    deps.user(request)
    project = deps.project(request, slug)
    return project.audit(limit=max(1, min(limit, 5000)), user=user, action=action)

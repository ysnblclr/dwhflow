"""Diyagram planı, çalıştırmalar, karşılaştırma ve üretilen dosyaların sunulması."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse

from ..convert import _plan
from ..project import NotFound, StoreError
from ..project.diff import diff_models
from ..project.runs import render_run, run_model, run_options, start_run
from . import deps
from .routes_projects import _state

router = APIRouter()


@router.get("/api/projects/{slug}/diagrams/plan")
def plan(request: Request, slug: str) -> dict[str, Any]:
    deps.user(request)
    project = deps.project(request, slug)
    state = _state(request, project)
    options = run_options(project)
    notes: list[str] = []
    pieces = _plan(state.model, options, notes) if state.model.datasets else []
    return {
        "slices": [{"id": p.id, "kind": p.kind, "title": p.title, "subtitle": p.subtitle,
                    "datasets": len(p.datasets), "processes": len(p.processes), "trimmed": p.trimmed}
                   for p in pieces],
        "notes": notes, "settings": project.settings().diyagram.model_dump(mode="json"),
    }


@router.post("/api/projects/{slug}/runs")
def create_run(request: Request, slug: str, body: Annotated[dict[str, Any] | None, Body()] = None) -> dict[str, Any]:
    user = deps.editor(request)
    project = deps.project(request, slug)
    ctx = deps.ctx(request)
    quality = (body or {}).get("quality")
    if quality not in (None, "showcase", "standard"):
        raise HTTPException(400, "kalite 'showcase' ya da 'standard' olmalı")
    state = _state(request, project)
    run_id, folder = start_run(project, user=user["username"])
    job = ctx.jobs.submit(
        "diyagram", project=project.slug, user=user["username"], title="diyagram üretimi",
        fn=lambda progress: _summary(render_run(project, state, folder, engine=ctx.renderer, quality=quality,
                                                progress=progress), run_id),
        run=run_id,
    )
    return {"run": run_id, "job": job}


def _summary(report: dict[str, Any], run_id: str) -> dict[str, Any]:
    diagrams = report.get("diagrams", [])
    return {"run": run_id, "ready": sum(1 for d in diagrams if d.get("html")), "total": len(diagrams),
            "notes": report.get("notes", [])}


@router.get("/api/projects/{slug}/runs")
def runs(request: Request, slug: str) -> list[dict[str, Any]]:
    deps.user(request)
    return deps.project(request, slug).runs()


@router.get("/api/projects/{slug}/runs/{run_id}")
def run_detail(request: Request, slug: str, run_id: str) -> dict[str, Any]:
    deps.user(request)
    project = deps.project(request, slug)
    from ..project.store import read_json

    try:
        folder = project.run_dir(run_id)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"id": run_id, "status": read_json(folder / "durum.json", {}) or {},
            "report": read_json(folder / "dwhflow-run.json", None),
            "job": deps.ctx(request).jobs.for_run(project.slug, run_id),
            "base_url": f"/dosya/{slug}/{run_id}/"}


@router.delete("/api/projects/{slug}/runs/{run_id}")
def delete_run(request: Request, slug: str, run_id: str) -> dict[str, Any]:
    user = deps.editor(request)
    project = deps.project(request, slug)
    try:
        project.delete_run(run_id, user=user["username"])
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except StoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"deleted": run_id}


@router.get("/api/projects/{slug}/compare")
def compare(request: Request, slug: str, a: str, b: str = "guncel") -> dict[str, Any]:
    """İki çalıştırmanın (ya da bir çalıştırma ile güncel modelin) farkı."""
    deps.user(request)
    project = deps.project(request, slug)
    try:
        before = run_model(project, a)
        after = _state(request, project).model if b == "guncel" else run_model(project, b)
    except (NotFound, FileNotFoundError) as exc:
        raise HTTPException(404, "çalıştırma bulunamadı ya da model kopyası yok") from exc
    return {"a": a, "b": b, **diff_models(before, after)}


_TYPES = {".html": "text/html; charset=utf-8", ".json": "application/json", ".yaml": "text/plain; charset=utf-8",
          ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml", ".png": "image/png"}


@router.get("/dosya/{slug}/{run_id}/{path:path}")
def run_file(request: Request, slug: str, run_id: str, path: str) -> FileResponse:
    deps.user(request)
    project = deps.project(request, slug)
    try:
        root = project.run_dir(run_id).resolve()
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    target = (root / (path or "index.html")).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404, "dosya bulunamadı")
    return FileResponse(target, media_type=_TYPES.get(Path(target).suffix.lower(), "application/octet-stream"))

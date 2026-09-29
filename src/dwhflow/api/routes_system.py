"""Sistem durumu, biçim kataloğu, etiketler, örnekler ve şablonlar."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from .. import __version__
from ..ingest import PLUGIN_ERRORS
from ..ingest.registry import REGISTRY, catalog
from ..project import samples as samples_mod
from ..render.engine import EngineError, node_version
from . import deps
from .views import meta as meta_payload

router = APIRouter()


@router.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": __version__}


@router.get("/api/system")
def system(request: Request) -> dict[str, Any]:
    current = deps.user(request)
    ctx = deps.ctx(request)
    node_path, node_major = node_version()
    engine: dict[str, Any] = {"installed": False, "ok": False}
    renderer = ctx.renderer
    if renderer is not None:
        engine["version"] = renderer.lock.get("version")
        try:
            renderer.ensure_installed()
            engine["installed"] = True
            renderer.node()
            engine["ok"] = True
        except EngineError as exc:
            engine["error"] = str(exc)
    settings = ctx.workspace.global_settings()
    payload = {
        "version": __version__, "node": {"path": node_path, "major": node_major}, "engine": engine,
        "formats": len(REGISTRY.formats), "plugin_errors": PLUGIN_ERRORS,
        "llm_profiles": sorted((settings.get("yz", {}) or {}).get("profiller", {}) or {"yerel": {}}),
    }
    if current["role"] == "yonetici":
        payload["workspace"] = str(ctx.workspace.root)
        payload["users"] = len(ctx.users.list())
    return payload


@router.get("/api/formats")
def formats(request: Request) -> dict[str, Any]:
    deps.user(request)
    fallback = REGISTRY.fallback
    return {"formats": catalog(),
            "unsupported": [{"suffix": k, "message": v} for k, v in sorted(REGISTRY.unsupported.items())],
            "archives": sorted(REGISTRY.expanders),
            "fallback": fallback.as_dict() if fallback else None}


@router.get("/api/meta")
def meta(request: Request) -> dict[str, Any]:
    deps.user(request)
    return meta_payload()


@router.get("/api/samples")
def samples(request: Request) -> dict[str, Any]:
    deps.user(request)
    return samples_mod.catalog()


@router.get("/api/samples/{group}/{name}")
def sample_file(request: Request, group: str, name: str) -> FileResponse:
    deps.user(request)
    try:
        path = samples_mod.sample_path(group, name)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@router.get("/api/templates/{template_id}")
def template(request: Request, template_id: str) -> Response:
    deps.user(request)
    try:
        name, data = samples_mod.template(template_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})

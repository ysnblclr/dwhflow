"""Ayarlar: genel (yönetici) ve proje (düzenleyici); etki önizlemesi, ad testi, rehber."""

from __future__ import annotations

from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import Response

from ..ingest import ingest_files
from ..ingest.base import explain_name
from ..ingest.config import DEFAULT, IngestConfig
from ..project import StoreError
from ..project import guide as guide_mod
from ..project import settings as settings_mod
from ..project.diff import diff_models
from ..project.settings import SettingsError
from . import deps
from .routes_projects import _state, submit_build

router = APIRouter()


def _payload(global_level: dict[str, Any], project_level: dict[str, Any] | None) -> dict[str, Any]:
    return {**settings_mod.with_origins(global_level, project_level), "global": global_level,
            "project": project_level}


@router.get("/api/settings")
def get_global(request: Request) -> dict[str, Any]:
    deps.user(request)
    try:
        return _payload(deps.ctx(request).workspace.global_settings(), None)
    except (SettingsError, StoreError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/api/settings")
def put_global(request: Request, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    deps.admin(request)
    workspace = deps.ctx(request).workspace
    try:
        level = workspace.save_global_settings(body.get("patch") or {})
    except (SettingsError, StoreError) as exc:
        raise HTTPException(400, str(exc)) from exc
    for project in workspace.projects():
        deps.ctx(request).engine.invalidate(project)
    return _payload(level, None)


@router.get("/api/projects/{slug}/settings")
def get_project_settings(request: Request, slug: str) -> dict[str, Any]:
    deps.user(request)
    project = deps.project(request, slug)
    try:
        return _payload(project.workspace.global_settings(), project.project_settings())
    except (SettingsError, StoreError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/api/projects/{slug}/settings")
def put_project_settings(request: Request, slug: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    user = deps.editor(request)
    project = deps.project(request, slug)
    patch = body.get("patch") or {}
    if not isinstance(patch, dict):
        raise HTTPException(400, "patch bir sözlük olmalı")
    if "yz" in patch and "profiller" in (patch["yz"] or {}) and user["role"] != "yonetici":
        raise HTTPException(403, "LLM profillerini yalnız yönetici tanımlar (genel ayarlar)")
    try:
        before = project.settings().ingest_config().fingerprint()
        level = project.save_settings(patch, user=user["username"])
    except (SettingsError, StoreError) as exc:
        raise HTTPException(400, str(exc)) from exc
    deps.ctx(request).engine.invalidate(project)
    job = None
    if project.settings().ingest_config().fingerprint() != before:
        job = submit_build(request, project, user["username"], reason="okuma ayarları değişti")
    return {**_payload(project.workspace.global_settings(), level), "job": job}


@router.post("/api/projects/{slug}/settings/preview")
def preview_settings(request: Request, slug: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """Okuma ayarı değişikliğinin etkisi, kaydetmeden (US-2.2): dokümanlar yeni ayarla yeniden okunur."""
    deps.editor(request)
    project = deps.project(request, slug)
    try:
        level = settings_mod.apply_patch(project.project_settings(), body.get("patch") or {})
        new_settings = settings_mod.effective(project.workspace.global_settings(), level)
    except (SettingsError, StoreError) as exc:
        raise HTTPException(400, str(exc)) from exc
    state = _state(request, project)
    paths = [path for path, _ in project.current_documents()]
    result = ingest_files(paths, name=project.meta().get("ad", slug), config=new_settings.ingest_config())
    before = {r["file"]: r for r in state.base.ingest.get("documents", [])}
    documents = []
    for report in result.reports:
        old = before.get(report.file, {})
        new = report.as_dict()
        changed = {k: (old.get(k), new[k]) for k in ("datasets", "processes", "flows", "status", "leftovers")
                   if old.get(k) != new[k]}
        if changed:
            documents.append({"file": report.file, "changes": changed})
    return {"documents": documents, "model": diff_models(state.base.model, result.model),
            "unrecognized_tables": len(result.unrecognized_tables),
            "before_unrecognized": len(state.base.ingest.get("unrecognized_tables", []))}


@router.post("/api/projects/{slug}/settings/name-test")
def name_test(request: Request, slug: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """Ad → katman/tür test kutusu (US-2.3). ``okuma`` verilirse kaydedilmemiş kurallarla dener."""
    deps.user(request)
    project = deps.project(request, slug)
    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "ad boş olamaz")
    try:
        reading = project.settings().okuma.model_dump(mode="json")
        if isinstance(body.get("okuma"), dict):
            reading = settings_mod.ReadingSettings.model_validate(
                {**reading, **body["okuma"]}).model_dump(mode="json")
        config = IngestConfig.from_settings(reading)
    except (ValueError, SettingsError) as exc:
        raise HTTPException(400, str(exc)) from exc
    result = explain_name(name, config)
    return {"name": name, "layer": result["layer"].value if result["layer"] else None,
            "kind": result["kind"].value if result["kind"] else None, "reason": result["reason"],
            "rule": result["rule"]}


@router.get("/api/projects/{slug}/settings/export")
def export_settings(request: Request, slug: str) -> Response:
    deps.user(request)
    project = deps.project(request, slug)
    text = yaml.safe_dump(project.project_settings(), allow_unicode=True, sort_keys=False)
    return Response(text, media_type="text/yaml; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{slug}-ayarlar.yaml"'})


@router.post("/api/projects/{slug}/settings/import")
def import_settings(request: Request, slug: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """Ayar profilini içe aktarır. ``apply=False`` ise yalnız farkı döner (US-2.8 önizleme)."""
    user = deps.editor(request)
    project = deps.project(request, slug)
    try:
        incoming = yaml.safe_load(str(body.get("yaml", ""))) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(400, f"YAML okunamadı: {exc}") from exc
    if not isinstance(incoming, dict):
        raise HTTPException(400, "ayar dosyası bir sözlük olmalı")
    try:
        settings_mod.effective(project.workspace.global_settings(), incoming)
    except SettingsError as exc:
        raise HTTPException(400, str(exc)) from exc
    current = project.project_settings()
    old_leaves, new_leaves = settings_mod._leaves(current), settings_mod._leaves(incoming)
    changes = [{"path": p, "old": old_leaves.get(p), "new": new_leaves.get(p)}
               for p in sorted(set(old_leaves) | set(new_leaves)) if old_leaves.get(p) != new_leaves.get(p)]
    if not body.get("apply"):
        return {"changes": changes, "applied": False}
    try:
        project.replace_settings(incoming, user=user["username"])
    except (SettingsError, StoreError) as exc:
        raise HTTPException(400, str(exc)) from exc
    deps.ctx(request).engine.invalidate(project)
    job = submit_build(request, project, user["username"], reason="ayar profili içe aktarıldı")
    return {"changes": changes, "applied": True, "job": job}


@router.get("/api/guide")
def default_guide(request: Request, format: str = "json") -> Any:
    deps.user(request)
    return _guide(guide_mod.build(DEFAULT), format, "dwhflow-bicim-rehberi")


@router.get("/api/projects/{slug}/guide")
def project_guide(request: Request, slug: str, format: str = "json") -> Any:
    deps.user(request)
    project = deps.project(request, slug)
    try:
        config = project.settings().ingest_config()
    except SettingsError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _guide(guide_mod.build(config), format, f"{slug}-bicim-rehberi")


def _guide(data: dict[str, Any], format: str, filename: str) -> Any:
    if format == "md":
        return Response(guide_mod.to_markdown(data), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}.md"'})
    return data

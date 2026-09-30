"""Projeler, pano, dokümanlar, okuma işleri ve arama."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from ..ingest import preview_document
from ..ingest.base import fold
from ..ingest.registry import REGISTRY, accepted_suffixes, spec_for
from ..project import NotFound, Project, StoreError
from ..project import samples as samples_mod
from ..project.corrections import rejected_suggestions
from ..project.review import paragraph_key
from ..project.settings import SettingsError
from . import deps
from .views import ModelView

router = APIRouter()
MAX_UPLOAD_FILES = 100


def _accepted() -> set[str] | None:
    """Genel dönüştürücü kuruluysa her uzantı kabul edilir (içerik okunur, hiçbir şey çalıştırılmaz)."""
    fallback = REGISTRY.fallback
    if fallback is not None and fallback.is_available():
        return None
    return set(accepted_suffixes())


def submit_build(request: Request, project: Project, user: str, *, use_llm: bool | None = None,
                 force: bool = False, reason: str = "yeniden oku") -> dict[str, Any]:
    ctx = deps.ctx(request)
    if use_llm is None:
        use_llm = project.settings().yz.etkin
    return ctx.jobs.submit(
        "okuma", project=project.slug, user=user, title=f"{reason}{' (LLM)' if use_llm else ''}",
        fn=lambda progress: ctx.engine.build(project, use_llm=bool(use_llm), user=user, progress=progress,
                                             force=force).summary(project),
    )


def _state(request: Request, project: Project):
    try:
        return deps.ctx(request).engine.state(project)
    except (SettingsError, StoreError) as exc:
        raise HTTPException(400, str(exc)) from exc


# -- projeler --------------------------------------------------------------- #


@router.get("/api/projects")
def list_projects(request: Request) -> list[dict[str, Any]]:
    deps.user(request)
    ctx = deps.ctx(request)
    items = []
    for project in ctx.workspace.projects():
        info = ctx.engine.info(project)
        items.append({**project.meta(), "documents": len(project.documents()), "runs": len(project.runs()),
                      "built_at": info.get("built_at"), "jobs": len(ctx.jobs.active(project.slug))})
    return items


@router.post("/api/projects")
def create_project(request: Request, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    user = deps.editor(request)
    ctx = deps.ctx(request)
    sample = body.get("sample")
    try:
        files = samples_mod.scenario_files(str(sample)) if sample else []
    except LookupError as exc:  # proje oluşturulmadan önce: yarım proje kalmasın
        raise HTTPException(404, str(exc)) from exc
    try:
        project = ctx.workspace.create_project(str(body.get("name", "")), str(body.get("description", "")),
                                               user=user["username"])
    except StoreError as exc:
        raise HTTPException(400, str(exc)) from exc
    job = None
    if files:
        for path in files:
            with path.open("rb") as handle:
                project.store_upload(path.name, handle, user=user["username"])
        job = submit_build(request, project, user["username"], use_llm=False, reason="örnek dokümanlar")
    return {**project.meta(), "job": job}


@router.get("/api/projects/{slug}")
def get_project(request: Request, slug: str) -> dict[str, Any]:
    deps.user(request)
    project = deps.project(request, slug)
    state = _state(request, project)
    return {**project.meta(), "summary": state.summary(project),
            "jobs": deps.ctx(request).jobs.active(project.slug)}


@router.patch("/api/projects/{slug}")
def update_project(request: Request, slug: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    user = deps.editor(request)
    project = deps.project(request, slug)
    try:
        return project.update_meta(user=user["username"], name=body.get("name"),
                                   description=body.get("description"))
    except StoreError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/api/projects/{slug}")
def delete_project(request: Request, slug: str) -> dict[str, Any]:
    deps.admin(request)
    ctx = deps.ctx(request)
    project = deps.project(request, slug)
    if ctx.jobs.active(slug):
        raise HTTPException(409, "projede süren iş var; bitince silinebilir")
    ctx.engine.forget(project)
    ctx.workspace.delete_project(slug)
    return {"deleted": slug}


@router.get("/api/projects/{slug}/dashboard")
def dashboard(request: Request, slug: str) -> dict[str, Any]:
    deps.user(request)
    ctx = deps.ctx(request)
    project = deps.project(request, slug)
    state = _state(request, project)
    documents = state.base.ingest.get("documents", [])
    problems = [d for d in documents if d.get("status") in ("error", "unsupported", "empty")]
    runs = project.runs()
    return {
        **project.meta(), "summary": state.summary(project),
        "problems": problems[:20], "orphans": state.applied.orphans[:20],
        "recent": project.audit(limit=12), "last_run": runs[0] if runs else None,
        "jobs": ctx.jobs.list(project.slug, limit=5),
    }


# -- dokümanlar ------------------------------------------------------------- #


def _reports_by_document(state) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for report in state.base.ingest.get("documents", []):
        out.setdefault(report.get("container") or report["file"], []).append(report)
    return out


@router.get("/api/projects/{slug}/documents")
def list_documents(request: Request, slug: str) -> list[dict[str, Any]]:
    deps.user(request)
    project = deps.project(request, slug)
    state = _state(request, project)
    reports = _reports_by_document(state)
    forced = project.settings().okuma.okuyucu_zorla
    items = []
    for document in project.documents():
        mine = reports.get(document["name"], [])
        main = next((r for r in mine if r["file"] == document["name"]), mine[0] if mine else None)
        items.append({**document, "report": main, "inner": [r for r in mine if r is not main],
                      "forced_reader": forced.get(document["name"]), "read": bool(mine)})
    return items


@router.post("/api/projects/{slug}/documents")
async def upload_documents(request: Request, slug: str,
                           files: Annotated[list[UploadFile], File()]) -> dict[str, Any]:
    user = deps.editor(request)
    project = deps.project(request, slug)
    if not files:
        raise HTTPException(400, "en az bir doküman gerekli")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(400, f"tek seferde en çok {MAX_UPLOAD_FILES} dosya")
    accepted = _accepted()
    stored, errors = [], []
    for upload in files:
        try:
            stored.append(project.store_upload(upload.filename or "dosya", upload.file, user=user["username"],
                                               accepted=accepted))
        except StoreError as exc:
            errors.append(str(exc))
    job = None
    if any(not item.get("unchanged") for item in stored):
        job = submit_build(request, project, user["username"], reason=f"{len(stored)} doküman yüklendi")
    if not stored:
        raise HTTPException(400, "; ".join(errors) or "dosya kaydedilemedi")
    return {"stored": stored, "errors": errors, "job": job}


@router.post("/api/projects/{slug}/documents/sample")
def add_sample_document(request: Request, slug: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    user = deps.editor(request)
    project = deps.project(request, slug)
    try:
        path = samples_mod.sample_path(str(body.get("group", "")), str(body.get("name", "")))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    with path.open("rb") as handle:
        stored = project.store_upload(path.name, handle, user=user["username"])
    job = submit_build(request, project, user["username"], reason="örnek doküman eklendi")
    return {"stored": [stored], "job": job}


@router.get("/api/projects/{slug}/documents/{name}")
def document_detail(request: Request, slug: str, name: str) -> dict[str, Any]:
    deps.user(request)
    project = deps.project(request, slug)
    try:
        versions = project.document_versions(name)
    except (NotFound, StoreError) as exc:
        raise HTTPException(404, "doküman bulunamadı") from exc
    state = _state(request, project)
    return {"name": name, "versions": versions, "reports": _reports_by_document(state).get(name, []),
            "forced_reader": project.settings().okuma.okuyucu_zorla.get(name)}


@router.get("/api/projects/{slug}/documents/{name}/preview")
def document_preview(request: Request, slug: str, name: str, version: int | None = None) -> dict[str, Any]:
    deps.user(request)
    project = deps.project(request, slug)
    try:
        path = project.document_path(name, version)
    except (NotFound, StoreError) as exc:
        raise HTTPException(404, "doküman bulunamadı") from exc
    settings = project.settings()
    fmt, blocks = preview_document(path, settings.ingest_config())
    state = _state(request, project)
    view = ModelView(state)
    items = []
    for node in list(state.model.datasets.values()) + list(state.model.processes.values()):
        for source in node.sources:
            if source.file == path.name:
                items.append({"type": node.node_type.value, "id": node.id, "name": node.name,
                              "status": node.status.value, "locator": source.locator,
                              "evidence": source.evidence, "extractor": source.extractor,
                              "confidence": source.confidence})
    for edge in state.model.edges:
        for source in edge.sources:
            if source.file == path.name:
                items.append({"type": "edge", "id": edge.id, "kind": edge.kind.value,
                              "name": f"{view.name(edge.source_node)} → {view.name(edge.target_node)}",
                              "status": edge.status.value, "locator": source.locator,
                              "evidence": source.evidence, "extractor": source.extractor,
                              "confidence": source.confidence})
    closed = rejected_suggestions(state.corrections)
    leftovers = []
    for file, loc, text, *_ in state.base.ingest.get("leftovers", []):
        if file == path.name:
            key = paragraph_key(file, loc, text)
            leftovers.append({"locator": loc, "text": text, "key": key, "closed": key in closed})
    spec = spec_for(fmt) if fmt else None
    return {"name": name, "format": fmt, "label": spec.label if spec else None, "blocks": blocks,
            "items": items, "leftovers": leftovers}


@router.get("/api/projects/{slug}/documents/{name}/download")
def download_document(request: Request, slug: str, name: str, version: int | None = None) -> FileResponse:
    deps.user(request)
    project = deps.project(request, slug)
    try:
        path = project.document_path(name, version)
    except (NotFound, StoreError) as exc:
        raise HTTPException(404, "doküman bulunamadı") from exc
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@router.delete("/api/projects/{slug}/documents/{name}")
def delete_document(request: Request, slug: str, name: str) -> dict[str, Any]:
    user = deps.editor(request)
    project = deps.project(request, slug)
    try:
        project.delete_document(name, user=user["username"])
    except (NotFound, StoreError) as exc:
        raise HTTPException(404, "doküman bulunamadı") from exc
    return {"deleted": name, "job": submit_build(request, project, user["username"], reason="doküman silindi")}


@router.put("/api/projects/{slug}/documents/{name}/reader")
def force_reader(request: Request, slug: str, name: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    user = deps.editor(request)
    project = deps.project(request, slug)
    reader = body.get("reader")
    current = dict(project.settings().okuma.okuyucu_zorla)
    if reader:
        if spec_for(str(reader)) is None:
            raise HTTPException(400, f"tanınmayan okuyucu: {reader}")
        current[name] = str(reader)
    else:
        current.pop(name, None)
    try:
        project.save_settings({"okuma": {"okuyucu_zorla": current or None}}, user=user["username"])
    except SettingsError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"forced_reader": current.get(name),
            "job": submit_build(request, project, user["username"], reason="okuyucu değişti")}


# -- okuma işleri ------------------------------------------------------------ #


@router.post("/api/projects/{slug}/build")
def build(request: Request, slug: str, body: Annotated[dict[str, Any] | None, Body()] = None) -> dict[str, Any]:
    user = deps.editor(request)
    project = deps.project(request, slug)
    body = body or {}
    use_llm = body.get("llm")
    if use_llm:
        try:
            project.settings().llm_config().check_allowed()
        except Exception as exc:  # noqa: BLE001 - LLMError/SettingsError kullanıcıya gösterilir
            raise HTTPException(400, str(exc)) from exc
    return submit_build(request, project, user["username"], use_llm=use_llm, force=bool(body.get("force")))


@router.get("/api/jobs/{job_id}")
def get_job(request: Request, job_id: str) -> dict[str, Any]:
    deps.user(request)
    job = deps.ctx(request).jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "iş bulunamadı")
    return job


@router.get("/api/projects/{slug}/jobs")
def project_jobs(request: Request, slug: str) -> list[dict[str, Any]]:
    deps.user(request)
    deps.project(request, slug)
    return deps.ctx(request).jobs.list(slug)


# -- arama ------------------------------------------------------------------ #


@router.get("/api/projects/{slug}/search")
def search(request: Request, slug: str, q: str = "") -> list[dict[str, Any]]:
    deps.user(request)
    project = deps.project(request, slug)
    needle = fold(q)
    if len(needle) < 2:
        return []
    state = _state(request, project)
    results = []
    for dataset in state.model.datasets.values():
        if needle in fold(dataset.name):
            results.append({"type": "dataset", "id": dataset.id, "name": dataset.name,
                            "detail": dataset.layer.value})
    for process in state.model.processes.values():
        if needle in fold(process.name):
            results.append({"type": "process", "id": process.id, "name": process.name,
                            "detail": process.tool or process.kind.value})
    for document in project.documents():
        if needle in fold(document["name"]):
            results.append({"type": "document", "id": document["name"], "name": document["name"],
                            "detail": f"sürüm {document['version']}"})
    results.sort(key=lambda r: (not fold(r["name"]).startswith(needle), len(r["name"]), r["name"]))
    return results[:25]

"""Onay kuyruğu ve doğrulama bulguları."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Request

from ..ingest.config import DEFAULT_ROLES
from ..project import CorrectionError
from ..project.corrections import rejected_suggestions
from ..project.review import paragraph_key
from ..project.settings import SettingsError
from . import deps
from .routes_projects import _state, submit_build
from .views import ModelView

router = APIRouter()


def _paragraph_beyond_cap(state, key: Any) -> dict[str, Any] | None:
    """Kuyruk tür başına sınırlıdır; sınırın ötesindeki açık paragraf da (doküman ekranından)
    kapatılabilsin."""
    if not isinstance(key, str) or not key.startswith("paragraf:") or key in rejected_suggestions(state.corrections):
        return None
    for file, locator, text, *_ in state.base.ingest.get("leftovers", []):
        if paragraph_key(file, locator, text) == key:
            return {"key": key, "type": "paragraf", "accept_ops": [],
                    "reject_ops": [{"islem": "oneri_reddet", "hedef": {"anahtar": key}}]}
    return None


@router.get("/api/projects/{slug}/review")
def review(request: Request, slug: str, type: str | None = None) -> dict[str, Any]:
    deps.user(request)
    state = _state(request, deps.project(request, slug))
    items = [item for item in state.review if type is None or item["type"] == type]
    from ..project.review import counts

    return {"items": items, "counts": counts(state.review)}


@router.post("/api/projects/{slug}/review/decide")
def decide(request: Request, slug: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """Kararları düzeltmelere çevirir. Tümü birlikte uygulanır; biri geçersizse hiçbiri kaydedilmez."""
    user = deps.editor(request)
    project = deps.project(request, slug)
    ctx = deps.ctx(request)
    decisions = body.get("decisions") or []
    if not isinstance(decisions, list) or not decisions:
        raise HTTPException(400, "en az bir karar gerekli")
    state = _state(request, project)
    items = {item["key"]: item for item in state.review}
    ops: list[dict[str, Any]] = []
    synonyms: dict[str, list[str]] = {}
    for decision in decisions:
        item = items.get(decision.get("key")) or _paragraph_beyond_cap(state, decision.get("key"))
        if item is None:
            raise HTTPException(409, f"öğe artık kuyrukta değil: {decision.get('key')}")
        verdict = decision.get("decision")
        if verdict == "reject":
            ops.extend(item["reject_ops"])
        elif verdict == "accept":
            if item["type"] == "tablo":
                mapping = decision.get("mapping") or {}
                if not isinstance(mapping, dict) or not mapping:
                    raise HTTPException(400, "tablo için başlık → rol eşlemesi gerekli")
                for header, role in mapping.items():
                    if header not in item["headers"]:
                        raise HTTPException(400, f"başlık tabloda yok: {header}")
                    if role not in DEFAULT_ROLES:
                        raise HTTPException(400, f"bilinmeyen rol: {role}")
                    synonyms.setdefault(role, []).append(str(header).strip())
            elif item["type"] == "paragraf":
                raise HTTPException(400, "paragraftaki akışı bağ ekle formuyla girin; paragrafı kapatmak "
                                         "için 'reddet' seçin")
            else:
                ops.extend(item["accept_ops"])
        else:
            raise HTTPException(400, "karar 'accept' ya da 'reject' olmalı")
    created = []
    if ops:
        try:
            created = ctx.engine.add_corrections(project, ops, user=user["username"], reason=body.get("reason"))
        except CorrectionError as exc:
            raise HTTPException(400, str(exc)) from exc
    job = None
    if synonyms:
        level = project.project_settings().get("okuma", {}).get("es_anlamlilar", {}) or {}
        merged = {role: list(values) for role, values in level.items()}
        for role, headers in synonyms.items():
            merged[role] = list(dict.fromkeys([*merged.get(role, []), *headers]))
        try:
            project.save_settings({"okuma": {"es_anlamlilar": merged}}, user=user["username"])
        except SettingsError as exc:
            raise HTTPException(400, str(exc)) from exc
        job = submit_build(request, project, user["username"], reason="tablo başlıkları eşlendi")
    return {"created": created, "settings_changed": bool(synonyms), "job": job,
            "summary": ctx.engine.state(project).summary(project)}


@router.get("/api/projects/{slug}/findings")
def findings(request: Request, slug: str) -> dict[str, Any]:
    deps.user(request)
    state = _state(request, deps.project(request, slug))
    view = ModelView(state)
    reasons = {(str(op.hedef.get("kural")), str(op.hedef.get("ozne") or "")): op
               for op in state.corrections if op.islem == "bulgu_yoksay"}
    items = [{**view.finding_row(f), "ignored": False} for f in state.findings]
    for finding in state.ignored:
        op = reasons.get((finding.rule, finding.subject or ""))
        items.append({**view.finding_row(finding), "ignored": True,
                      "ignore": {"id": op.id, "reason": op.neden, "by": op.kim, "at": op.zaman} if op else None})
    from ..validate import summarize

    return {"items": items, "summary": summarize(state.findings), "ignored": len(state.ignored)}

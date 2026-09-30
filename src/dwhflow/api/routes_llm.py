"""Yapay zekâ: profil testi, istem kütüphanesi, test konsolu, maskeleme önizlemesi.

Güvenlik: istek gövdesi hiçbir zaman adres/anahtar taşıyamaz; yalnız **kayıtlı** bir profilin
adı verilir (bkz. inceleme #14). Dış adresler kayıtlı profilde açıkça izinliyse çağrılır ve
o durumda maskeleme zorunludur.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Request

from ..llm import (
    LLMClient,
    LLMError,
    PromptError,
    list_prompts,
    load_prompt,
    mask_text,
    run_console,
    save_prompt_version,
)
from ..project import settings as settings_mod
from ..project.settings import SettingsError
from . import deps

router = APIRouter()


def _settings(request: Request, slug: str | None) -> settings_mod.Settings:
    ctx = deps.ctx(request)
    try:
        if slug:
            return deps.project(request, slug).settings()
        return settings_mod.effective(ctx.workspace.global_settings())
    except SettingsError as exc:
        raise HTTPException(400, str(exc)) from exc


def _client(request: Request, settings: settings_mod.Settings, profile: str | None) -> LLMClient:
    try:
        config = settings.llm_config(profile)
    except SettingsError as exc:
        raise HTTPException(400, str(exc)) from exc
    config.enabled = True
    try:
        config.check_allowed()
    except LLMError as exc:
        raise HTTPException(400, str(exc)) from exc
    factory = deps.ctx(request).engine.llm_factory
    return factory(config) if factory else LLMClient(config)


@router.post("/api/llm/test")
def test_profile(request: Request, body: Annotated[dict[str, Any] | None, Body()] = None) -> dict[str, Any]:
    deps.editor(request)
    body = body or {}
    settings = _settings(request, body.get("project"))
    client = _client(request, settings, body.get("profile"))
    try:
        return client.probe()
    except Exception as exc:  # noqa: BLE001 - bağlantı hatası kullanıcıya gösterilir
        return {"ok": False, "error": str(exc)}


@router.get("/api/llm/prompts")
def prompts(request: Request) -> list[dict[str, Any]]:
    deps.user(request)
    return list_prompts(deps.ctx(request).workspace.prompts_dir)


@router.get("/api/llm/prompts/{prompt_id}")
def prompt(request: Request, prompt_id: str, version: int | None = None) -> dict[str, Any]:
    deps.user(request)
    try:
        return load_prompt(prompt_id, version, deps.ctx(request).workspace.prompts_dir)
    except PromptError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/api/llm/prompts/{prompt_id}")
def save_prompt(request: Request, prompt_id: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """Yeni istem sürümü (yönetici). Eski sürümler korunur; çıktılar hangi sürümle üretildiğini taşır."""
    user = deps.admin(request)
    workspace = deps.ctx(request).workspace
    try:
        version = save_prompt_version(prompt_id, body.get("prompt") or {}, workspace.prompts_dir,
                                      author=user["username"])
    except PromptError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": prompt_id, "version": version}


@router.post("/api/llm/console")
def console(request: Request, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """Bir metni istemle dener; modele dokunmaz. ``dry_run`` ise yalnız gönderilecek metni gösterir."""
    deps.editor(request)
    text = str(body.get("text") or "")
    if not text.strip():
        raise HTTPException(400, "metin boş olamaz")
    if len(text) > 20000:
        raise HTTPException(400, "metin en çok 20 000 karakter olabilir")
    settings = _settings(request, body.get("project"))
    client = _client(request, settings, body.get("profile"))
    version = body.get("version")
    try:
        return run_console(client, text, prompt_id=str(body.get("prompt_id") or "extract_flows"),
                           version=int(version) if version else None,
                           user_dir=deps.ctx(request).workspace.prompts_dir,
                           mask=bool(body.get("mask", settings.yz.maske)),
                           doc_type=str(body.get("doc_type") or "metin"), dry_run=bool(body.get("dry_run")))
    except PromptError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/llm/mask")
def mask_preview(request: Request, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    deps.user(request)
    masked, counts = mask_text(str(body.get("text") or ""))
    return {"masked": masked, "counts": counts}

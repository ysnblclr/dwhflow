"""Giriş, ilk kurulum, parola ve kullanıcı yönetimi."""

from __future__ import annotations

import ipaddress
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Request, Response

from ..auth import ROLE_LABELS
from ..project import StoreError
from . import deps

router = APIRouter()


def _loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def _set_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(deps.COOKIE, token, httponly=True, samesite="strict", path="/",
                        secure=request.url.scheme == "https", max_age=12 * 3600)


@router.get("/api/auth/state")
def state(request: Request) -> dict[str, Any]:
    ctx = deps.ctx(request)
    if not ctx.auth:
        return {"auth": False, "setup_required": False, "setup_allowed": False,
                "user": getattr(request.state, "user", None), "roles": ROLE_LABELS}
    return {"auth": True, "setup_required": ctx.users.empty(),
            "setup_allowed": ctx.users.empty() and _loopback(request),
            "user": getattr(request.state, "user", None), "roles": ROLE_LABELS}


def _no_login(request: Request) -> None:
    if not deps.ctx(request).auth:
        raise HTTPException(409, "giriş şu an kapalı (sunucu --girissiz çalışıyor); "
                                 "kullanıcılar Ayarlar › Kullanıcılar'dan yönetilir")


@router.post("/api/auth/setup")
def setup(request: Request, response: Response, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """İlk yönetici. Yalnız hiç kullanıcı yokken ve sunucunun kendisinden (127.0.0.1) yapılabilir."""
    _no_login(request)
    ctx = deps.ctx(request)
    if not ctx.users.empty():
        raise HTTPException(409, "kurulum zaten yapılmış")
    if not _loopback(request):
        raise HTTPException(403, "ilk kurulum yalnız sunucu makinesinden yapılabilir "
                                 "(ya da: dwhflow kullanici ekle --rol yonetici)")
    try:
        created = ctx.users.create(body.get("username", ""), body.get("name", ""), "yonetici",
                                   body.get("password", ""))
    except StoreError as exc:
        raise HTTPException(400, str(exc)) from exc
    _set_cookie(request, response, ctx.sessions.create(created["username"]))
    return {"user": created}


@router.post("/api/auth/login")
def login(request: Request, response: Response, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    _no_login(request)
    ctx = deps.ctx(request)
    username = str(body.get("username", "")).strip().lower()
    address = request.client.host if request.client else "?"
    keys = (f"u:{username}", f"a:{address}")
    wait = ctx.throttle.blocked_for(*keys)
    if wait:
        raise HTTPException(429, f"çok fazla hatalı deneme; {wait} saniye sonra tekrar deneyin")
    account = ctx.users.authenticate(username, str(body.get("password", "")))
    if account is None:
        ctx.throttle.failure(*keys)
        raise HTTPException(401, "kullanıcı adı ya da parola hatalı")
    ctx.throttle.success(*keys)
    _set_cookie(request, response, ctx.sessions.create(account["username"]))
    return {"user": account}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict[str, Any]:
    token = request.cookies.get(deps.COOKIE)
    if token:
        deps.ctx(request).sessions.revoke(token)
    response.delete_cookie(deps.COOKIE, path="/")
    return {"ok": True}


@router.post("/api/auth/password")
def change_password(request: Request, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    _no_login(request)
    ctx = deps.ctx(request)
    current = deps.user(request)
    if ctx.users.authenticate(current["username"], str(body.get("current", ""))) is None:
        raise HTTPException(400, "mevcut parola hatalı")
    try:
        ctx.users.update(current["username"], password=str(body.get("new", "")))
    except StoreError as exc:
        raise HTTPException(400, str(exc)) from exc
    ctx.sessions.revoke_user(current["username"], keep=request.cookies.get(deps.COOKIE))
    return {"ok": True}


# -- kullanıcılar (yönetici) ------------------------------------------------ #


@router.get("/api/users")
def list_users(request: Request) -> list[dict[str, Any]]:
    deps.admin(request)
    return deps.ctx(request).users.list()


@router.post("/api/users")
def create_user(request: Request, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    deps.admin(request)
    try:
        return deps.ctx(request).users.create(body.get("username", ""), body.get("name", ""),
                                              body.get("role", "izleyici"), body.get("password", ""))
    except StoreError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/api/users/{username}")
def update_user(request: Request, username: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    current = deps.admin(request)
    ctx = deps.ctx(request)
    if username == current["username"] and (body.get("active") is False or
                                            (body.get("role") and body["role"] != "yonetici")):
        raise HTTPException(400, "kendi yöneticiliğinizi kaldıramazsınız")
    try:
        updated = ctx.users.update(username, name=body.get("name"), role=body.get("role"),
                                   active=body.get("active"), password=body.get("password") or None)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except StoreError as exc:
        raise HTTPException(400, str(exc)) from exc
    if body.get("password") or body.get("active") is False or body.get("role"):
        ctx.sessions.revoke_user(username)
    return updated


@router.delete("/api/users/{username}")
def delete_user(request: Request, username: str) -> dict[str, Any]:
    current = deps.admin(request)
    if username == current["username"]:
        raise HTTPException(400, "kendi hesabınızı silemezsiniz")
    ctx = deps.ctx(request)
    try:
        ctx.users.delete(username)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except StoreError as exc:
        raise HTTPException(400, str(exc)) from exc
    ctx.sessions.revoke_user(username)
    return {"deleted": username}

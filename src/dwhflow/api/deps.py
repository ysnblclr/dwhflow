"""Uygulama bağlamı ve yetki bağımlılıkları."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request

from ..auth import LoginThrottle, Sessions, UserStore, role_at_least
from ..project import NotFound, Project, ProjectEngine, Workspace
from .jobs import JobRunner

COOKIE = "dwhflow_oturum"


@dataclass
class AppContext:
    workspace: Workspace
    engine: ProjectEngine
    users: UserStore
    sessions: Sessions
    throttle: LoginThrottle
    jobs: JobRunner
    #: Archify motoru (testlerde sahte motor verilir)
    renderer: Any = None


def ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def user(request: Request) -> dict[str, Any]:
    current = getattr(request.state, "user", None)
    if current is None:
        raise HTTPException(401, "oturum açılmamış")
    return current


def require(request: Request, role: str) -> dict[str, Any]:
    current = user(request)
    if not role_at_least(current["role"], role):
        labels = {"duzenleyici": "düzenleyici", "yonetici": "yönetici"}
        raise HTTPException(403, f"bu işlem için {labels.get(role, role)} yetkisi gerekli")
    return current


def editor(request: Request) -> dict[str, Any]:
    return require(request, "duzenleyici")


def admin(request: Request) -> dict[str, Any]:
    return require(request, "yonetici")


def project(request: Request, slug: str) -> Project:
    try:
        return ctx(request).workspace.project(slug)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc

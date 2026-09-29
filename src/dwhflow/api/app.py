"""Web uygulaması (ekip kullanımı): projeler, dokümanlar, model, düzeltmeler, diyagramlar, ayarlar.

Güvenlik:

* ``Host`` başlığı denetlenir; yalnız yerel adlar ve ``--allow-host`` ile verilenler kabul
  edilir (DNS rebinding).
* Değişiklik yapan her istek ``X-DWHFlow: 1`` başlığı taşımak zorundadır. Başka bir sitenin
  sayfası bu başlığı ancak CORS ön kontrolüyle gönderebilir; sunucu CORS izni vermez (CSRF).
* ``/api`` (sağlık, giriş ve ilk kurulum dışında) ve ``/dosya`` oturum ister. Oturum çerezi
  HttpOnly ve SameSite=Strict'tir; roller uç noktalarda denetlenir.
* Arayüz sayfası sıkı bir içerik güvenliği politikasıyla (CSP) sunulur; dış kaynak yüklenmez.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..auth import LoginThrottle, Sessions, UserStore
from ..project import ProjectEngine, Workspace
from ..render.engine import Engine
from . import (
    deps,
    routes_auth,
    routes_diagrams,
    routes_model,
    routes_projects,
    routes_review,
    routes_settings,
    routes_system,
)
from .jobs import JobRunner

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
CSRF_HEADER = "x-dwhflow"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
PUBLIC_API = {"/api/health", "/api/auth/state", "/api/auth/login", "/api/auth/setup"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "frame-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
       "frame-ancestors 'self'")


def _host_name(header: str) -> str:
    """``Host`` başlığından port'suz ad: ``127.0.0.1:8765`` → ``127.0.0.1``, ``[::1]:80`` → ``[::1]``."""
    header = header.strip().lower()
    if header.startswith("["):
        return header.split("]", 1)[0] + "]"
    return header.rsplit(":", 1)[0]


def create_app(data_dir: str | Path | None = None, *, engine: Any = None,
               allowed_hosts: set[str] | None = None, llm_factory: Any = None,
               workers: int = 2) -> FastAPI:
    """``engine``: Archify motoru (verilmezse sabitlenmiş motor). ``llm_factory``: testlerde sahte LLM."""
    workspace = Workspace(data_dir or Path.cwd() / ".dwhflow")
    context = deps.AppContext(
        workspace=workspace, engine=ProjectEngine(llm_factory=llm_factory),
        users=UserStore(workspace.users_file), sessions=Sessions(), throttle=LoginThrottle(),
        jobs=JobRunner(workers=workers), renderer=engine if engine is not None else Engine(),
    )
    hosts = LOCAL_HOSTS | {h.lower() for h in (allowed_hosts or set())}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        context.jobs.shutdown()

    app = FastAPI(title="DWHFlow", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json",
                  lifespan=lifespan)
    app.state.ctx = context

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if _host_name(request.headers.get("host") or "") not in hosts:
            return JSONResponse({"detail": "Bu sunucu bu adla erişime kapalı (Host reddedildi)."}, status_code=421)
        if request.method in MUTATING and request.headers.get(CSRF_HEADER) != "1":
            return JSONResponse({"detail": f"Eksik güvenlik başlığı: {CSRF_HEADER}"}, status_code=403)
        path = request.url.path
        session = context.sessions.get(request.cookies.get(deps.COOKIE))
        account = context.users.get(session.username) if session else None
        request.state.user = account if account and account.get("active") else None
        protected = (path.startswith("/api/") and path not in PUBLIC_API) or path.startswith("/dosya/")
        if protected and request.state.user is None:
            return JSONResponse({"detail": "oturum açılmamış"}, status_code=401)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        if not path.startswith("/dosya/"):
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response

    for module in (routes_system, routes_auth, routes_projects, routes_model, routes_review,
                   routes_diagrams, routes_settings):
        app.include_router(module.router)
    try:
        from . import routes_llm

        app.include_router(routes_llm.router)
    except ImportError:  # LLM uçları henüz yoksa uygulama yine çalışsın
        pass

    if STATIC_DIR.is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            response = FileResponse(STATIC_DIR / "index.html")
            response.headers["Content-Security-Policy"] = CSP
            response.headers["Cache-Control"] = "no-cache"
            return response

    return app

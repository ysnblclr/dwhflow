"""Web uygulaması: dokümanları yükle → akışa çevir → sonuçları aç.

Güvenlik (incelemedeki CSRF ve DNS rebinding açıklarının kapatılması):

* ``Host`` başlığı denetlenir; yalnız yerel adlar (ve ``--allow-host`` ile açıkça
  verilenler) kabul edilir. DNS rebinding ile dışarıdan okuma engellenir.
* Değişiklik yapan her istek ``X-DWHFlow: 1`` başlığı taşımak zorundadır. Başka bir
  sitenin sayfası bu başlığı ancak CORS ön kontrolünden (preflight) geçerek
  gönderebilir; sunucu CORS izni vermediği için tarayıcı isteği hiç göndermez.
  Böylece kötü niyetli bir sayfa dosya yükleyemez, çalışma silemez, ayar değiştiremez.
* Sunucu varsayılan olarak yalnız ``127.0.0.1``'e bağlanır.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..convert import RunOptions, convert
from ..ingest import ACCEPTED, READERS
from ..llm import LLMClient
from ..render.engine import Engine, EngineError, node_version
from .workspace import Workspace, WorkspaceError

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
CSRF_HEADER = "x-dwhflow"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def _host_name(header: str) -> str:
    """``Host`` başlığından port'suz ad: ``127.0.0.1:8765`` → ``127.0.0.1``, ``[::1]:80`` → ``[::1]``."""
    header = header.strip().lower()
    if header.startswith("["):
        return header.split("]", 1)[0] + "]"
    return header.rsplit(":", 1)[0]


def _parse_paths(text: str) -> list[tuple[str, str]]:
    """``KAYNAK > HEDEF`` satırlarını çiftlere çevirir."""
    pairs = []
    for line in (text or "").splitlines():
        for separator in ("->", "→", ">", "=>"):
            if separator in line:
                source, target = (part.strip() for part in line.split(separator, 1))
                if source and target:
                    pairs.append((source, target))
                break
    return pairs[:10]


class Runner:
    """Dönüştürmeleri arka planda, sırayla çalıştırır (her biri kendi içinde paralel)."""

    def __init__(self, workspace: Workspace, engine: Engine | None) -> None:
        self.workspace = workspace
        self.engine = engine
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dwhflow-run")
        self.lock = threading.Lock()

    def submit(self, run_id: str, folder: Path, files: list[Path], options: RunOptions,
               use_llm: bool) -> None:
        status = {
            "state": "queued", "title": options.title, "inputs": [f.name for f in files],
            "created_at": datetime.now().isoformat(timespec="seconds"), "progress": [],
            "use_llm": use_llm,
        }
        self.workspace.write_status(folder, status)
        self.pool.submit(self._run, folder, files, options, use_llm, status)

    def _run(self, folder: Path, files: list[Path], options: RunOptions, use_llm: bool,
             status: dict[str, Any]) -> None:
        def progress(stage: str, detail: str) -> None:
            with self.lock:
                status["state"] = "running"
                status["progress"].append({"stage": stage, "detail": detail})
                self.workspace.write_status(folder, status)

        try:
            llm = None
            if use_llm:
                config = self.workspace.llm_config()
                config.check_allowed()
                llm = LLMClient(config)
            convert(files, folder, llm=llm, options=options, engine=self.engine, progress=progress)
            status["state"] = "done"
        except Exception as exc:  # noqa: BLE001 - hata kullanıcıya status'ta gösterilir
            status["state"] = "error"
            status["error"] = f"{type(exc).__name__}: {exc}"
        self.workspace.write_status(folder, status)


def create_app(data_dir: str | Path | None = None, *, engine: Engine | None = None,
               allowed_hosts: set[str] | None = None) -> FastAPI:
    workspace = Workspace(data_dir or Path.cwd() / ".dwhflow")
    engine = engine or Engine()
    runner = Runner(workspace, engine)
    hosts = LOCAL_HOSTS | {h.lower() for h in (allowed_hosts or set())}

    app = FastAPI(title="DWHFlow", version=__version__, docs_url="/api/docs",
                  openapi_url="/api/openapi.json")
    app.state.workspace = workspace
    app.state.runner = runner

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if _host_name(request.headers.get("host") or "") not in hosts:
            return JSONResponse({"detail": "Bu sunucu yalnız yerel erişime açık (Host reddedildi)."},
                                status_code=421)
        if request.method in MUTATING and request.headers.get(CSRF_HEADER) != "1":
            return JSONResponse({"detail": f"Eksik güvenlik başlığı: {CSRF_HEADER}"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    # -- durum -------------------------------------------------------------- #

    @app.get("/api/health")
    def health() -> dict:
        node_path, node_major = node_version()
        engine_state: dict[str, Any] = {"version": engine.lock["version"], "installed": False}
        try:
            engine.ensure_installed()
            engine_state["installed"] = True
            engine.node()
            engine_state["ok"] = True
        except EngineError as exc:
            engine_state["ok"] = False
            engine_state["error"] = str(exc)
        llm = workspace.llm_config()
        return {
            "version": __version__,
            "engine": engine_state,
            "node": {"path": node_path, "major": node_major},
            "llm": {"enabled": llm.enabled, "model": llm.model, "local": llm.is_local},
            "accepted": ACCEPTED,
            "readable": sorted(READERS),
        }

    # -- dönüştürmeler ------------------------------------------------------ #

    @app.post("/api/runs")
    async def create_run(
        files: Annotated[list[UploadFile], File()],
        title: Annotated[str, Form()] = "",
        use_llm: Annotated[bool, Form()] = False,
        quality: Annotated[str, Form()] = "showcase",
        paths: Annotated[str, Form()] = "",
    ) -> dict:
        if not files:
            raise HTTPException(400, "en az bir doküman gerekli")
        if len(files) > 50:
            raise HTTPException(400, "tek seferde en fazla 50 doküman")
        if quality not in ("showcase", "standard"):
            raise HTTPException(400, "kalite 'showcase' ya da 'standard' olmalı")
        if use_llm:
            config = workspace.llm_config()
            if not config.enabled:
                raise HTTPException(400, "Yapay zekâ ayarlarda kapalı; önce Ayarlar'dan etkinleştirin")
            try:
                config.check_allowed()
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(400, str(exc)) from exc
        run_id, folder = workspace.new_run()
        stored: list[Path] = []
        try:
            for upload in files:
                stored.append(workspace.store_upload(folder, upload.filename or "dosya", upload.file))
        except WorkspaceError as exc:
            workspace.delete_run(run_id, force=True)
            raise HTTPException(400, str(exc)) from exc
        options = RunOptions(title=title.strip() or stored[0].stem, quality=quality,
                             paths=_parse_paths(paths))
        runner.submit(run_id, folder, stored, options, use_llm)
        return {"id": run_id}

    @app.get("/api/runs")
    def list_runs() -> list[dict]:
        return workspace.list_runs()

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        try:
            return workspace.read_status(run_id)
        except WorkspaceError as exc:
            raise HTTPException(400, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, "çalışma bulunamadı") from exc

    @app.delete("/api/runs/{run_id}")
    def delete_run(run_id: str) -> dict:
        try:
            workspace.delete_run(run_id)
        except WorkspaceError as exc:
            raise HTTPException(400, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, "çalışma bulunamadı") from exc
        return {"deleted": run_id}

    # -- ayarlar ------------------------------------------------------------ #

    @app.get("/api/settings")
    def get_settings() -> dict:
        return workspace.settings()

    @app.put("/api/settings")
    def put_settings(patch: Annotated[dict[str, Any], Body()]) -> dict:
        try:
            return workspace.save_settings(patch)
        except WorkspaceError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/llm/test")
    def test_llm() -> dict:
        """Kayıtlı ayarla bağlantı testi. İstek gövdesi ayarı ezemez (bkz. inceleme #14)."""
        config = workspace.llm_config()
        try:
            return LLMClient(config).probe()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    # -- statik ------------------------------------------------------------- #

    app.mount("/runs", StaticFiles(directory=workspace.runs, html=True), name="runs")
    if STATIC_DIR.is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    return app

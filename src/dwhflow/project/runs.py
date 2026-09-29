"""Proje diyagram çalıştırmaları: güncel (düzeltilmiş) modelden Archify diyagramları ve galeri.

Her çalıştırma kendi klasöründedir; modelin o anki kopyası (``model.json``) saklanır,
böylece iki çalıştırma sonradan karşılaştırılabilir (US-7.4).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import __version__
from ..convert import RunOptions, render_diagrams
from ..model import Model
from ..render.engine import Engine
from ..render.gallery import write_gallery
from ..validate import summarize
from .pipeline import ProjectState
from .store import Project, read_json, write_json


def run_options(project: Project, *, quality: str | None = None) -> RunOptions:
    diagram = project.settings().diyagram
    return RunOptions(
        quality=quality or diagram.kalite, jobs=diagram.paralellik,
        paths=[tuple(pair) for pair in diagram.yollar],
        focus=[(item.nesne, item.adim) for item in diagram.cevreler],
        auto=diagram.otomatik.genel or diagram.otomatik.soy or diagram.otomatik.batch,
        overview=diagram.otomatik.genel, lineage=diagram.otomatik.soy, batch=diagram.otomatik.batch,
        budget=diagram.butce, job_budget=diagram.job_butcesi, hidden=list(diagram.gizli),
        title=project.meta().get("ad") or project.slug,
    )


def start_run(project: Project, *, user: str) -> tuple[str, Path]:
    run_id, folder = project.new_run()
    write_json(folder / "durum.json", {"state": "queued", "created_by": user,
                                       "created_at": datetime.now().isoformat(timespec="seconds")})
    project.log(user, "diyagram_uret", {"calistirma": run_id})
    return run_id, folder


def render_run(project: Project, state: ProjectState, folder: Path, *, engine: Engine | None,
               quality: str | None = None, progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    say = progress or (lambda message: None)
    status = read_json(folder / "durum.json", {}) or {}
    status["state"] = "running"
    write_json(folder / "durum.json", status)
    started = time.perf_counter()
    try:
        model = state.model
        (folder / "model.json").write_text(model.model_dump_json(), encoding="utf-8")
        options = run_options(project, quality=quality)
        documents = state.base.ingest.get("documents", [])
        report: dict[str, Any] = {
            "tool": f"DWHFlow {__version__}",
            "created_at": status.get("created_at") or datetime.now().isoformat(timespec="seconds"),
            "created_by": status.get("created_by"),
            "title": options.title,
            "inputs": [d.get("file") for d in documents if d.get("format") != "arsiv"],
            "ingest": {"documents": documents, "llm": state.base.ingest.get("llm"),
                       "llm_candidates": len(state.base.ingest.get("leftovers", [])),
                       "stats": model.stats()},
            "validation": {
                "summary": summarize(state.findings),
                "findings": [{"rule": f.rule, "severity": f.severity.value, "subject": f.subject,
                              "message": f.message} for f in state.findings],
            },
            "corrections": len(state.corrections),
            "diagrams": [], "notes": [], "engine": None,
        }
        render_diagrams(model, report, folder, findings=state.findings, options=options, engine=engine,
                        progress=lambda stage, detail: say(detail))
        report["duration_s"] = round(time.perf_counter() - started, 1)
        write_json(folder / "dwhflow-run.json", report)
        write_gallery(report, folder / "index.html")
        status.update(state="done", finished_at=datetime.now().isoformat(timespec="seconds"),
                      ready=sum(1 for d in report["diagrams"] if d.get("html")), total=len(report["diagrams"]))
        write_json(folder / "durum.json", status)
        return report
    except Exception as exc:
        status.update(state="error", error=f"{type(exc).__name__}: {exc}",
                      finished_at=datetime.now().isoformat(timespec="seconds"))
        write_json(folder / "durum.json", status)
        raise


def run_model(project: Project, run_id: str) -> Model:
    folder = project.run_dir(run_id)
    return Model.model_validate_json((folder / "model.json").read_text(encoding="utf-8"))

"""Dönüştürme hattı: dokümanlar → model → doğrulama → dilimler → Archify HTML'leri.

Archify'ın akışıyla aynı disiplin:

* Her dönüştürme **kendi klasörüne** yazar; önceki çalışmalar korunur, hiçbir
  şey mevcut bir modelin üzerine yazılmaz.
* Her diyagram iki kapıdan geçer: DWHFlow alan doğrulaması (modelin anlamı) ve
  Archify ``finalize`` (şema, yerleşim, teslim, tarayıcı).
* Showcase kalitesi tutmazsa diyagram standart kaliteyle bir kez daha denenir;
  o da tutmazsa "başarısız" olarak raporlanır — asla başarılı gösterilmez.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .ingest import IngestResult, ingest_files
from .model import Model
from .render.compile import compile_batch, compile_slice
from .render.engine import Engine, EngineError, FinalizeResult
from .render.gallery import write_gallery
from .render.repair import apply_label_fixes
from .render.slices import (
    Slice,
    dataset_graph,
    dataset_links,
    plan_default,
    slice_focus,
    slice_path,
)
from .store import save_model
from .validate import summarize, validate

Progress = Callable[[str, str], None]

#: Motorun önerdiği etiket düzeltmeleriyle en çok kaç kez yeniden denenir.
MAX_REPAIR_ROUNDS = 2


@dataclass
class RunOptions:
    quality: str = "showcase"
    render: bool = True
    paths: list[tuple[str, str]] = field(default_factory=list)
    focus: list[tuple[str, int]] = field(default_factory=list)
    auto: bool = True
    jobs: int = 2
    title: str | None = None


def _plan(model: Model, options: RunOptions, notes: list[str]) -> list[Slice]:
    links = dataset_links(model)
    graph = dataset_graph(model, links)
    plan: list[Slice] = []
    for source, target in options.paths:
        try:
            plan.append(slice_path(model, graph, source, target))
        except KeyError as exc:
            notes.append(f"yol diyagramı üretilemedi: {exc}")
    for center, hops in options.focus:
        try:
            plan.append(slice_focus(model, graph, center, hops))
        except KeyError as exc:
            notes.append(f"çevre diyagramı üretilemedi: {exc}")
    if options.auto:
        known = {frozenset(s.datasets) for s in plan}
        plan.extend(s for s in plan_default(model) if frozenset(s.datasets) not in known)
    # Aynı id'ye sahip dilimler (ör. iki kez istenen yol) tek dosyaya düşmesin.
    seen: dict[str, int] = {}
    for piece in plan:
        count = seen.get(piece.id, 0)
        seen[piece.id] = count + 1
        if count:
            piece.id = f"{piece.id}-{count + 1}"
    return plan


def _render_one(engine: Engine, model: Model, piece: Slice, out_dir: Path, findings: list[Any],
                links: dict, quality: str) -> dict[str, Any]:
    """Bir dilimi üretir. Her diyagram kendi klasöründe: Archify teslim sırasında çıktı
    dizinini kilitler; paralel finalize'lar aynı dizini paylaşırsa birbirini düşürür.

    Sıra: istenen kalite → (motorun önerdiği etiket onarımları) → standart kalite.
    Her deneme ayrı alt klasöre yazar: Archify bir çıktıyı ilk adayına bağlar ve
    aynı yere ikinci bir kanıt dosyası yazmayı reddeder."""
    attempts: list[FinalizeResult] = []
    repairs = 0
    base = out_dir / "diagrams" / piece.id
    for level in dict.fromkeys([quality, "standard"]):  # showcase → standard (tekrarsız)
        if piece.kind == "batch":
            spec = compile_batch(model, piece, output=f"{piece.id}.html", quality=level,
                                 findings=findings)
        else:
            spec = compile_slice(model, piece, output=f"{piece.id}.html", quality=level,
                                 findings=findings, all_links=links)
        for round_ in range(MAX_REPAIR_ROUNDS + 1):
            if level == quality:
                folder = base if round_ == 0 else base / f"onarim-{round_}"
            else:
                folder = base / (level if round_ == 0 else f"{level}-onarim-{round_}")
            folder.mkdir(parents=True, exist_ok=True)
            spec_path = folder / f"{piece.id}.json"
            html_path = folder / f"{piece.id}.html"
            spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
            result = engine.finalize("architecture", spec_path, html_path, quality=level)
            attempts.append(result)
            if result.delivered or round_ == MAX_REPAIR_ROUNDS:
                break
            fixed = apply_label_fixes(spec, result.diagnostics)
            if not fixed:
                break
            repairs += fixed
        if attempts[-1].delivered:
            break
    final = attempts[-1]
    html = final.html.relative_to(out_dir).as_posix() if final.delivered and final.html else None
    return {
        "id": piece.id,
        "title": piece.title,
        "subtitle": piece.subtitle,
        "kind": piece.kind,
        "datasets": len(piece.datasets) or len(piece.processes),
        "trimmed": piece.trimmed,
        "html": html,
        "status": final.status,
        "summary": final.summary_tr(),
        "quality": final.quality,
        "gates": final.gates,
        "browser_verified": final.browser_verified,
        "repairs": repairs,
        "attempts": [a.as_dict() for a in attempts],
    }


def _failed(piece: Slice, message: str) -> dict[str, Any]:
    return {
        "id": piece.id, "title": piece.title, "subtitle": piece.subtitle, "kind": piece.kind,
        "datasets": len(piece.datasets) or len(piece.processes), "trimmed": piece.trimmed,
        "html": None, "status": "error", "summary": message, "quality": None, "gates": {},
        "browser_verified": False, "repairs": 0, "attempts": [],
    }


def convert(
    files: list[Path],
    out_dir: Path,
    *,
    llm: Any = None,
    options: RunOptions | None = None,
    engine: Engine | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Dokümanları dönüştürür, ``out_dir`` içine her şeyi yazar ve rapor döner."""
    options = options or RunOptions()
    say = progress or (lambda stage, detail: None)
    started = time.perf_counter()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []

    say("okuma", f"{len(files)} doküman okunuyor")
    ingested: IngestResult = ingest_files(
        [Path(f) for f in files], llm=llm, name=options.title or "dokuman-modeli"
    )
    model = ingested.model

    say("model", f"{len(model.datasets)} veri nesnesi, {len(model.processes)} süreç")
    save_model(model, out_dir / "model")
    findings = validate(model)

    report: dict[str, Any] = {
        "tool": f"DWHFlow {__version__}",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "title": options.title or (Path(files[0]).stem if files else "DWHFlow"),
        "inputs": [Path(f).name for f in files],
        "ingest": ingested.as_dict(),
        "validation": {
            "summary": summarize(findings),
            "findings": [
                {"rule": f.rule, "severity": f.severity.value, "subject": f.subject, "message": f.message}
                for f in findings
            ],
        },
        "diagrams": [],
        "notes": notes,
        "engine": None,
    }

    if not model.datasets:
        notes.append("Dokümanlardan hiç tablo/dosya çıkarılamadı; diyagram üretilmedi. "
                      "Doküman raporundaki notlara bakın.")
    elif options.render:
        engine = engine or Engine()
        report["engine"] = {"name": engine.lock["name"], "version": engine.lock["version"]}
        plan = _plan(model, options, notes)
        say("diyagram", f"{len(plan)} diyagram üretilecek")
        links = dataset_links(model)
        try:
            engine.ensure_installed()
            engine.node()
        except EngineError as exc:
            notes.append(f"Diyagramlar üretilemedi: {exc}")
            plan = []
        with ThreadPoolExecutor(max_workers=max(1, options.jobs)) as pool:
            futures = [pool.submit(_render_one, engine, model, piece, out_dir, findings, links,
                                   options.quality) for piece in plan]
            for index, (piece, future) in enumerate(zip(plan, futures, strict=True), start=1):
                try:
                    item = future.result()
                except Exception as exc:  # bir diyagramın hatası diğerlerini düşürmesin
                    item = _failed(piece, f"derleme hatası: {exc}")
                report["diagrams"].append(item)
                say("diyagram", f"{index}/{len(plan)}: {item['title']} — {item['summary']}")
    else:
        notes.append("Görselleştirme kapalı (--no-render): yalnız model ve rapor üretildi.")

    report["duration_s"] = round(time.perf_counter() - started, 1)
    (out_dir / "dwhflow-run.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                              encoding="utf-8")
    write_gallery(report, out_dir / "index.html")
    say("bitti", f"{sum(1 for d in report['diagrams'] if d['html'])} diyagram hazır")
    return report

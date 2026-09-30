"""Proje derleme hattı: dokümanlar → temel model (önbellekli) → düzeltmeler → doğrulama → onay kuyruğu.

Pahalı adım dokümanların okunmasıdır (ve LLM). Sonucu, okumayı etkileyen her şeyin
özetinden türetilen bir anahtarla önbelleğe alınır: doküman sürümleri, okuma ayarları,
okuyucu sürümü, LLM profili. Elle yapılan bir düzeltme yalnız ucuz adımları yeniden
çalıştırır; böylece düzenlemeler anında yansır.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import __version__
from ..ingest import ingest_files
from ..ingest.config import using
from ..ingest.registry import REGISTRY
from ..model import Model, Status
from ..render.slices import dataset_links
from ..store import save_model
from ..validate import summarize, validate
from ..validate.rules import Finding
from . import corrections as corr
from . import review as review_mod
from .store import Project, read_json, write_json

Progress = Callable[[str], None]
KEEP_CACHES = 4


@dataclass
class BaseResult:
    key: str
    model: Model
    ingest: dict[str, Any]


@dataclass
class ProjectState:
    base: BaseResult
    model: Model
    corrections: list[corr.Correction]
    applied: corr.ApplyReport
    findings: list[Finding]
    ignored: list[Finding]
    review: list[dict[str, Any]]
    info: dict[str, Any]
    stale: bool
    computed_at: float = field(default_factory=time.time)

    def summary(self, project: Project) -> dict[str, Any]:
        links = dataset_links(self.model)
        documents = self.base.ingest.get("documents", [])
        return {
            "stats": {
                **self.model.stats(), "links": len(links),
                "draft_links": sum(1 for link in links.values() if link.draft),
                "draft_nodes": sum(1 for n in self.model.nodes.values() if n.status is Status.DRAFT),
            },
            "documents": {
                "total": len(project.documents()),
                "read": len([d for d in documents if d.get("format") != "arsiv"]),
                "problems": sum(1 for d in documents if d.get("status") in ("error", "unsupported", "empty")),
            },
            "findings": summarize(self.findings), "ignored_findings": len(self.ignored),
            "review": review_mod.counts(self.review),
            "corrections": {"total": len(self.corrections), "orphans": len(self.applied.orphans),
                            "errors": sum(1 for r in self.applied.results if r["durum"] == "hata")},
            "build": self.info, "stale": self.stale,
            "llm": self.base.ingest.get("llm"),
        }


def apply_confidence_threshold(model: Model, threshold: float) -> int:
    """Yalnız LLM'den gelen ve güveni eşiğin altındaki kenarları çıkarır (US-6 güven eşiği).

    Yapısal bir kaynağı da olan kenarlar ve LLM dışı nesneler dokunulmadan kalır; LLM'in
    tek başına eklediği ve kenarsız kalan taslak nesneler de çıkarılır. Çıkarılan kenar sayısını döner.
    """
    def weak(edge) -> bool:
        return edge.is_llm_only and max((s.confidence for s in edge.sources), default=0.0) < threshold

    before = len(model.edges)
    model.edges = [edge for edge in model.edges if not weak(edge)]
    connected = {e.source_node for e in model.edges} | {e.target_node for e in model.edges}
    for collection in (model.datasets, model.processes):
        for node_id in [i for i, node in collection.items()
                        if node.status is Status.DRAFT and i not in connected
                        and node.sources and all(s.is_llm for s in node.sources)]:
            del collection[node_id]
    return before - len(model.edges)


def _reader_version() -> str:
    return f"{__version__}:" + ",".join(sorted(REGISTRY.formats))


def base_key(project: Project, *, use_llm: bool) -> str:
    settings = project.settings()
    config = settings.ingest_config()
    docs = [(d["name"], d["sha256"]) for d in project.documents()]
    llm_part: Any = None
    if use_llm:
        llm = settings.llm_config()
        llm_part = [llm.base_url, llm.model, settings.yz.maske, settings.yz.tablo_eslemesi,
                    settings.yz.guven_esigi]
    blob = json.dumps({"docs": docs, "config": config.fingerprint(), "reader": _reader_version(),
                       "llm": llm_part}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


class ProjectEngine:
    """Proje durumlarını hesaplar ve bellekte tutar (sunucu başına bir tane)."""

    def __init__(self, llm_factory: Callable[[Any], Any] | None = None) -> None:
        #: LLMConfig → istemci. Testlerde sahte istemci verilir.
        self.llm_factory = llm_factory
        self._base: dict[str, BaseResult] = {}
        self._state: dict[str, tuple[str, ProjectState]] = {}

    # -- temel model -------------------------------------------------------- #

    def _cache_dir(self, project: Project, key: str) -> Path:
        return project.cache_dir / key

    def _load_cached(self, project: Project, key: str) -> BaseResult | None:
        cached = self._base.get(project.key)
        if cached and cached.key == key:
            return cached
        folder = self._cache_dir(project, key)
        model_file, ingest_file = folder / "model.json", folder / "okuma.json"
        if not (model_file.is_file() and ingest_file.is_file()):
            return None
        try:
            model = Model.model_validate_json(model_file.read_text(encoding="utf-8"))
            ingest = read_json(ingest_file, {})
        except (ValueError, OSError):
            return None
        result = BaseResult(key=key, model=model, ingest=ingest)
        self._base[project.key] = result
        return result

    def _llm_client(self, project: Project):
        settings = project.settings()
        config = settings.llm_config()
        config.enabled = True
        config.check_allowed()
        if self.llm_factory is not None:
            return self.llm_factory(config)
        from ..llm import LLMClient

        return LLMClient(config)

    def build(self, project: Project, *, use_llm: bool = False, user: str = "sistem",
              progress: Progress | None = None, force: bool = False) -> ProjectState:
        """Dokümanları (gerekirse) yeniden okur ve durumu yeniler."""
        say = progress or (lambda message: None)
        with project.lock:
            settings = project.settings()
            key = base_key(project, use_llm=use_llm)
            base = None if force else self._load_cached(project, key)
            if base is None:
                client = self._llm_client(project) if use_llm else None
                paths = [path for path, _ in project.current_documents()]
                say(f"{len(paths)} doküman okunuyor")
                llm_options = None
                if client is not None:
                    llm_options = {"user_prompt_dir": project.workspace.prompts_dir,
                                   "mask": settings.yz.maske}
                result = ingest_files(paths, llm=client, name=project.meta().get("ad", project.slug),
                                      config=settings.ingest_config(), progress=say, llm_options=llm_options)
                if client is not None and settings.yz.guven_esigi > 0:
                    dropped = apply_confidence_threshold(result.model, settings.yz.guven_esigi)
                    if result.llm is not None:
                        result.llm["below_threshold"] = dropped
                ingest = {
                    "documents": [r.as_dict() for r in result.reports],
                    "leftovers": [list(item) for item in result.leftovers],
                    "unrecognized_tables": [list(item) for item in result.unrecognized_tables],
                    "llm": result.llm, "errors": result.errors,
                    "table_suggestions": self._table_suggestions(client, result, settings,
                                                                 project.workspace.prompts_dir),
                }
                folder = self._cache_dir(project, key)
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "model.json").write_text(result.model.model_dump_json(), encoding="utf-8")
                write_json(folder / "okuma.json", ingest)
                base = BaseResult(key=key, model=result.model, ingest=ingest)
                self._base[project.key] = base
                self._prune(project, keep=key)
            write_json(project.model_dir / "durum.json", {
                "key": key, "llm": use_llm, "built_at": datetime.now().isoformat(timespec="seconds"),
                "built_by": user,
            })
            self._state.pop(project.key, None)
            say("model kuruldu")
            state = self.state(project)
            project.log(user, "yeniden_oku", {"llm": use_llm, "anahtar": key,
                                              "nesne": len(state.model.nodes)})
            return state

    def _table_suggestions(self, client, result, settings, prompts_dir) -> list[dict[str, Any]]:
        if client is None or not settings.yz.tablo_eslemesi or not result.unrecognized_tables:
            return []
        try:
            from ..llm.table_roles import suggest_for_unrecognized
        except ImportError:
            return []
        try:
            items = suggest_for_unrecognized(client, result.unrecognized_tables,
                                             user_prompt_dir=prompts_dir, mask=settings.yz.maske)
        except Exception:  # noqa: BLE001 - öneri üretilemezse tablo yine elle eşlenebilir
            return []
        for item in items:
            item.setdefault("key", f"tablo:{item.get('file')}:{item.get('locator')}")
        return items

    def _prune(self, project: Project, keep: str) -> None:
        if not project.cache_dir.is_dir():
            return
        folders = sorted((p for p in project.cache_dir.iterdir() if p.is_dir()),
                         key=lambda p: p.stat().st_mtime, reverse=True)
        for folder in folders[KEEP_CACHES:]:
            if folder.name != keep:
                shutil.rmtree(folder, ignore_errors=True)

    # -- durum -------------------------------------------------------------- #

    def info(self, project: Project) -> dict[str, Any]:
        return read_json(project.model_dir / "durum.json", {}) or {}

    def state(self, project: Project) -> ProjectState:
        """Son okumanın üstüne güncel düzeltmeleri uygular. Hiç okunmamışsa şimdi okur (LLM'siz)."""
        with project.lock:
            info = self.info(project)
            base = self._load_cached(project, info["key"]) if info.get("key") else None
            if base is None:
                return self.build(project, use_llm=False, user="sistem")
            raw = project.corrections()
            # Doküman sürümleri de imzada: yeni yükleme "model eskidi" uyarısını hemen göstermeli.
            documents = [(d["name"], d["sha256"]) for d in project.documents()]
            signature = hashlib.sha256(json.dumps([base.key, raw, project.project_settings(),
                                                   project.workspace.global_settings(), documents],
                                                  sort_keys=True, default=str).encode("utf-8")).hexdigest()
            cached = self._state.get(project.key)
            if cached and cached[0] == signature:
                return cached[1]
            settings = project.settings()
            ops = corr.parse(raw)
            with using(settings.ingest_config()):
                model, applied = corr.apply(base.model, ops)
                ignored_keys = corr.ignored_findings(ops)
                all_findings = validate(model)
                findings = [f for f in all_findings if (f.rule, f.subject or "") not in ignored_keys]
                ignored = [f for f in all_findings if (f.rule, f.subject or "") in ignored_keys]
                review = review_mod.compute(model, base.ingest, corr.rejected_suggestions(ops))
            stale = base_key(project, use_llm=bool(info.get("llm"))) != base.key
            state = ProjectState(base=base, model=model, corrections=ops, applied=applied,
                                 findings=findings, ignored=ignored, review=review, info=info, stale=stale)
            self._write_model(project, model)
            self._state[project.key] = (signature, state)
            return state

    def _write_model(self, project: Project, model: Model) -> None:
        """Düzeltilmiş modeli okunabilir YAML olarak yazar (git'e konabilir, dışa aktarılabilir)."""
        folder = project.model_dir / "yaml"
        if folder.exists():
            shutil.rmtree(folder)
        save_model(model, folder)

    def invalidate(self, project: Project) -> None:
        self._state.pop(project.key, None)

    def forget(self, project: Project) -> None:
        self._state.pop(project.key, None)
        self._base.pop(project.key, None)

    # -- düzeltme ekleme ------------------------------------------------------ #

    def add_corrections(self, project: Project, ops: list[dict[str, Any]], *, user: str,
                        reason: str | None = None) -> list[dict[str, Any]]:
        """İşlemleri doğrular (şu anki modelde denenir), kaydeder ve günlüğe yazar.

        Hepsi ya kaydedilir ya hiçbiri: biri geçersizse ``CorrectionError``.
        """
        if not ops:
            raise corr.CorrectionError("en az bir işlem gerekli")
        with project.lock:
            state = self.state(project)
            created: list[corr.Correction] = []
            next_number = int(project.next_correction_id()[2:])
            for offset, raw in enumerate(ops):
                try:
                    created.append(corr.Correction.model_validate({
                        "id": f"d-{next_number + offset:04d}", "islem": raw.get("islem"),
                        "hedef": raw.get("hedef") or {}, "deger": raw.get("deger") or {},
                        "neden": raw.get("neden") or reason, "kim": user,
                        "zaman": datetime.now().isoformat(timespec="seconds"),
                    }))
                except ValueError as exc:
                    raise corr.CorrectionError(f"geçersiz işlem: {exc}") from exc
            # Hepsi tek kopya üzerinde sırayla denenir; biri tutmazsa hiçbiri kaydedilmez.
            with using(project.settings().ingest_config()):
                _, report = corr.apply(state.model, created)
            for result in report.results:
                if result["durum"] != "uygulandi":
                    raise corr.CorrectionError(f"{corr.OP_LABELS[result['islem']]}: {result['mesaj']}")
            items = project.corrections() + [op.model_dump(exclude_none=True) for op in created]
            project.save_corrections(items)
            for op in created:
                project.log(user, "duzeltme", {"islem_kaydi": op.model_dump(exclude_none=True)})
            project.touch()
            self.invalidate(project)
            messages = report.by_id()
            # "sonuc" yalnız yanıtta döner (ör. "mevcut nesne onaylandı"); dosyaya yazılmaz.
            return [{**op.model_dump(exclude_none=True), "sonuc": messages[op.id]["mesaj"]} for op in created]

    def undo(self, project: Project, correction_id: str, *, user: str) -> dict[str, Any]:
        with project.lock:
            items = project.corrections()
            match = next((item for item in items if item.get("id") == correction_id), None)
            if match is None:
                raise corr.CorrectionError("düzeltme bulunamadı")
            project.save_corrections([item for item in items if item.get("id") != correction_id])
            project.log(user, "geri_al", {"islem_kaydi": match})
            project.touch()
            self.invalidate(project)
            return match

    def redo(self, project: Project, correction_id: str, *, user: str) -> dict[str, Any]:
        """Geri alınmış bir düzeltmeyi günlükteki kaydından yeniden ekler (sona)."""
        with project.lock:
            if any(item.get("id") == correction_id for item in project.corrections()):
                raise corr.CorrectionError("düzeltme zaten etkin")
            record = next((entry["veri"]["islem_kaydi"] for entry in project.audit(limit=None)
                           if entry.get("islem") == "geri_al"
                           and entry.get("veri", {}).get("islem_kaydi", {}).get("id") == correction_id), None)
            if record is None:
                raise corr.CorrectionError("geri alınmış böyle bir düzeltme yok")
            op = corr.Correction.model_validate(record)
            items = project.corrections() + [op.model_dump(exclude_none=True)]
            project.save_corrections(items)
            project.log(user, "yinele", {"islem_kaydi": record})
            project.touch()
            self.invalidate(project)
            return record

    def preview(self, project: Project, ops: list[dict[str, Any]], *, user: str) -> dict[str, Any]:
        """İşlemleri kaydetmeden uygular ve farkı döner (US-3.7)."""
        from .diff import diff_models

        state = self.state(project)
        trial: list[corr.Correction] = []
        for index, raw in enumerate(ops):
            trial.append(corr.Correction.model_validate({
                "id": f"d-{9_000_000 + index}", "islem": raw.get("islem"), "hedef": raw.get("hedef") or {},
                "deger": raw.get("deger") or {}, "kim": user,
                "zaman": datetime.now().isoformat(timespec="seconds"),
            }))
        with using(project.settings().ingest_config()):
            after, report = corr.apply(state.model, trial)
        result = diff_models(state.model, after)
        result["results"] = report.results
        return result

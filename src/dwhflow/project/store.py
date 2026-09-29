"""Çalışma alanı ve proje deposu (dosya sistemi üzerinde, git'e konabilir düz dosyalar).

    <çalışma alanı>/
      ayarlar.yaml                 genel ayarlar (yönetici)
      kullanicilar.yaml            kullanıcılar (parola özetleri; bkz. auth)
      istemler/                    kullanıcı istem sürümleri
      projeler/<kısa ad>/
        proje.yaml                 ad, açıklama, oluşturan, zamanlar
        ayarlar.yaml               projeye özel ayarlar (yalnız değişenler)
        duzeltmeler.yaml           elle düzeltmeler (sıralı işlem listesi)
        gunluk.jsonl               değişiklik günlüğü (yalnız ekleme)
        belgeler/<ad>/v<n>/<ad>    doküman sürümleri; belgeler/<ad>/surumler.yaml
        onbellek/<anahtar>/        okuma önbelleği (temel model + okuma raporu)
        model/                     son kurulan model (YAML) + durum.json
        calistirmalar/<kimlik>/    diyagram çalıştırmaları

Eşzamanlılık: sunucu tek süreçtir; her proje için bir ``RLock`` yazma işlemlerini
sıraya koyar. Dosyalar önce geçici ada yazılıp atomik olarak yerine taşınır.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

import yaml

from ..ingest.base import fold
from . import settings as settings_mod

MAX_FILE_BYTES = 64 * 1024 * 1024
_CHUNK = 1 << 20
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_RUN_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-f0-9]{6}$")
_SAFE = re.compile(r"[^A-Za-z0-9._\- ğüşıöçĞÜŞİÖÇ()]")


class StoreError(ValueError):
    """Kullanıcı girdisi reddedildi (HTTP 400)."""


class NotFound(LookupError):
    """İstenen proje/doküman/çalıştırma yok (HTTP 404)."""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def safe_filename(name: str) -> str:
    """Yol bileşenlerini ve sürpriz karakterleri atar. Her platformda aynı sonuç."""
    base = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _SAFE.sub("_", base).strip().strip(".").strip()
    if not cleaned or set(cleaned) <= {".", "_", " "}:
        raise StoreError(f"geçersiz dosya adı: {name!r}")
    return cleaned[:180]


def make_slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", fold(name)).strip("-")[:48] or "proje"
    return base


def read_yaml(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise StoreError(f"{path.name} okunamadı: {exc}") from exc
    return default if data is None else data


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                # Windows: okuyan başka bir tanıtıcı varken değiştirme kısa süre reddedilebilir.
                if attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_yaml(path: Path, data: Any) -> None:
    _atomic_write(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))


def write_json(path: Path, data: Any) -> None:
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=1, default=str))


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    for attempt in range(5):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, PermissionError):
            if attempt == 4:
                raise
            time.sleep(0.05)
    return default


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def lock_for(key: str) -> threading.RLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


# --------------------------------------------------------------------------- #
# Çalışma alanı
# --------------------------------------------------------------------------- #


class Workspace:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.projects_dir = self.root / "projeler"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.settings_file = self.root / "ayarlar.yaml"
        self.users_file = self.root / "kullanicilar.yaml"
        self.prompts_dir = self.root / "istemler"

    # -- genel ayarlar ------------------------------------------------------ #

    def global_settings(self) -> dict[str, Any]:
        data = read_yaml(self.settings_file, {})
        if not isinstance(data, dict):
            raise StoreError("ayarlar.yaml bir sözlük olmalı")
        return data

    def save_global_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        with lock_for(str(self.settings_file)):
            level = settings_mod.apply_patch(self.global_settings(), patch)
            settings_mod.effective(level)  # doğrula; hatalıysa yazma
            write_yaml(self.settings_file, level)
            return level

    # -- projeler ----------------------------------------------------------- #

    def projects(self) -> list[Project]:
        items = []
        for folder in sorted(self.projects_dir.iterdir()):
            if folder.is_dir() and _SLUG.match(folder.name) and (folder / "proje.yaml").is_file():
                items.append(Project(self, folder.name))
        return items

    def project(self, slug: str) -> Project:
        if not _SLUG.match(slug or ""):
            raise NotFound("proje bulunamadı")
        project = Project(self, slug)
        if not (project.dir / "proje.yaml").is_file():
            raise NotFound("proje bulunamadı")
        return project

    def create_project(self, name: str, description: str = "", *, user: str) -> Project:
        name = (name or "").strip()
        if not name or len(name) > 80:
            raise StoreError("proje adı 1–80 karakter olmalı")
        with lock_for(str(self.projects_dir)):
            base = make_slug(name)
            slug, index = base, 2
            while (self.projects_dir / slug).exists():
                slug, index = f"{base}-{index}", index + 1
            project = Project(self, slug)
            project.dir.mkdir(parents=True)
            write_yaml(project.dir / "proje.yaml", {
                "ad": name, "aciklama": description.strip()[:500], "olusturan": user,
                "olusturma": now(), "guncelleme": now(),
            })
            project.log(user, "proje_olustur", {"ad": name})
            return project

    def delete_project(self, slug: str) -> None:
        project = self.project(slug)
        with lock_for(project.key):
            shutil.rmtree(project.dir)


# --------------------------------------------------------------------------- #
# Proje
# --------------------------------------------------------------------------- #


class Project:
    def __init__(self, workspace: Workspace, slug: str) -> None:
        self.workspace = workspace
        self.slug = slug
        self.dir = workspace.projects_dir / slug
        self.key = str(self.dir)
        self.docs_dir = self.dir / "belgeler"
        self.cache_dir = self.dir / "onbellek"
        self.model_dir = self.dir / "model"
        self.runs_dir = self.dir / "calistirmalar"
        self.settings_file = self.dir / "ayarlar.yaml"
        self.corrections_file = self.dir / "duzeltmeler.yaml"
        self.log_file = self.dir / "gunluk.jsonl"

    @property
    def lock(self) -> threading.RLock:
        return lock_for(self.key)

    # -- kimlik ------------------------------------------------------------- #

    def meta(self) -> dict[str, Any]:
        data = read_yaml(self.dir / "proje.yaml", {})
        return {"slug": self.slug, **data}

    def update_meta(self, *, user: str, name: str | None = None, description: str | None = None) -> dict:
        with self.lock:
            data = read_yaml(self.dir / "proje.yaml", {})
            if name is not None:
                if not name.strip() or len(name) > 80:
                    raise StoreError("proje adı 1–80 karakter olmalı")
                data["ad"] = name.strip()
            if description is not None:
                data["aciklama"] = description.strip()[:500]
            data["guncelleme"] = now()
            write_yaml(self.dir / "proje.yaml", data)
            self.log(user, "proje_guncelle", {"ad": data.get("ad")})
            return self.meta()

    def touch(self) -> None:
        with self.lock:
            data = read_yaml(self.dir / "proje.yaml", {})
            data["guncelleme"] = now()
            write_yaml(self.dir / "proje.yaml", data)

    # -- ayarlar ------------------------------------------------------------ #

    def project_settings(self) -> dict[str, Any]:
        data = read_yaml(self.settings_file, {})
        if not isinstance(data, dict):
            raise StoreError("proje ayarlar.yaml bir sözlük olmalı")
        return data

    def settings(self) -> settings_mod.Settings:
        return settings_mod.effective(self.workspace.global_settings(), self.project_settings())

    def save_settings(self, patch: dict[str, Any], *, user: str) -> dict[str, Any]:
        with self.lock:
            level = settings_mod.apply_patch(self.project_settings(), patch)
            settings_mod.effective(self.workspace.global_settings(), level)
            write_yaml(self.settings_file, level)
            self.log(user, "ayar", {"yama": patch})
            self.touch()
            return level

    def replace_settings(self, level: dict[str, Any], *, user: str) -> dict[str, Any]:
        """Proje ayar katmanını bütünüyle değiştirir (ayar profili içe aktarma)."""
        with self.lock:
            settings_mod.effective(self.workspace.global_settings(), level)
            write_yaml(self.settings_file, level)
            self.log(user, "ayar_ice_aktar", {"anahtarlar": sorted(level)})
            self.touch()
            return level

    # -- dokümanlar --------------------------------------------------------- #

    def _doc_dir(self, name: str) -> Path:
        return self.docs_dir / safe_filename(name)

    def documents(self) -> list[dict[str, Any]]:
        if not self.docs_dir.is_dir():
            return []
        items = []
        for folder in sorted(self.docs_dir.iterdir(), key=lambda p: p.name.lower()):
            versions = read_yaml(folder / "surumler.yaml", {}).get("surumler") if folder.is_dir() else None
            if not versions:
                continue
            current = versions[-1]
            items.append({"name": folder.name, "suffix": Path(folder.name).suffix.lower(),
                          "version": current["n"], "versions": len(versions), "size": current["boyut"],
                          "sha256": current["sha256"], "uploaded_by": current["yukleyen"],
                          "uploaded_at": current["zaman"]})
        return items

    def document_versions(self, name: str) -> list[dict[str, Any]]:
        folder = self._doc_dir(name)
        versions = read_yaml(folder / "surumler.yaml", {}).get("surumler")
        if not versions:
            raise NotFound("doküman bulunamadı")
        return versions

    def document_path(self, name: str, version: int | None = None) -> Path:
        versions = self.document_versions(name)
        entry = versions[-1] if version is None else next((v for v in versions if v["n"] == version), None)
        if entry is None:
            raise NotFound("sürüm bulunamadı")
        return self._doc_dir(name) / f"v{entry['n']}" / safe_filename(name)

    def current_documents(self) -> list[tuple[Path, dict[str, Any]]]:
        return [(self.document_path(doc["name"]), doc) for doc in self.documents()]

    def store_upload(self, filename: str, stream: BinaryIO, *, user: str,
                     accepted: set[str] | None = None) -> dict[str, Any]:
        """Dokümanı boyut sınırıyla diske akıtır; aynı adla yüklenen dosya yeni sürüm olur."""
        name = safe_filename(filename)
        suffix = Path(name).suffix.lower()
        if accepted is not None and suffix not in accepted:
            raise StoreError(f"'{name}': desteklenmeyen uzantı ({suffix or 'yok'})")
        with self.lock:
            folder = self._doc_dir(name)
            folder.mkdir(parents=True, exist_ok=True)
            index = read_yaml(folder / "surumler.yaml", {}) or {}
            versions = index.get("surumler") or []
            number = (versions[-1]["n"] + 1) if versions else 1
            staging = folder / f".yukleniyor-{uuid.uuid4().hex[:8]}"
            digest, size = hashlib.sha256(), 0
            try:
                with staging.open("wb") as handle:
                    while chunk := stream.read(_CHUNK):
                        size += len(chunk)
                        if size > MAX_FILE_BYTES:
                            raise StoreError(f"'{name}' çok büyük (sınır {MAX_FILE_BYTES // 2**20} MB)")
                        digest.update(chunk)
                        handle.write(chunk)
                sha = digest.hexdigest()
                if versions and versions[-1]["sha256"] == sha:
                    staging.unlink()
                    return {"name": name, "version": versions[-1]["n"], "unchanged": True}
                target = folder / f"v{number}" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging, target)
            finally:
                if staging.exists():
                    staging.unlink()
            versions.append({"n": number, "boyut": size, "sha256": sha, "yukleyen": user, "zaman": now()})
            write_yaml(folder / "surumler.yaml", {"surumler": versions})
            self.log(user, "belge_yukle", {"belge": name, "surum": number, "boyut": size})
            self.touch()
            return {"name": name, "version": number, "unchanged": False}

    def delete_document(self, name: str, *, user: str) -> None:
        with self.lock:
            folder = self._doc_dir(name)
            if not (folder / "surumler.yaml").is_file():
                raise NotFound("doküman bulunamadı")
            shutil.rmtree(folder)
            self.log(user, "belge_sil", {"belge": safe_filename(name)})
            self.touch()

    # -- düzeltmeler -------------------------------------------------------- #

    def corrections(self) -> list[dict[str, Any]]:
        data = read_yaml(self.corrections_file, [])
        if not isinstance(data, list):
            raise StoreError("duzeltmeler.yaml bir liste olmalı")
        return data

    def save_corrections(self, items: list[dict[str, Any]]) -> None:
        with self.lock:
            write_yaml(self.corrections_file, items)

    def next_correction_id(self) -> str:
        used = {item.get("id") for item in self.corrections()}
        for entry in self.audit(limit=None):
            op = entry.get("veri", {}).get("islem_kaydi") or {}
            used.add(op.get("id"))
        number = 1 + max((int(str(i)[2:]) for i in used if str(i or "").startswith("d-")
                          and str(i)[2:].isdigit()), default=0)
        return f"d-{number:04d}"

    # -- günlük ------------------------------------------------------------- #

    def log(self, user: str, action: str, data: dict[str, Any] | None = None) -> None:
        entry = {"zaman": now(), "kim": user, "islem": action, "veri": data or {}}
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with self.lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            with self.log_file.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")

    def audit(self, *, limit: int | None = 500, user: str | None = None,
              action: str | None = None) -> list[dict[str, Any]]:
        if not self.log_file.is_file():
            return []
        entries = []
        for line in self.log_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if user and entry.get("kim") != user:
                continue
            if action and entry.get("islem") != action:
                continue
            entries.append(entry)
        entries.reverse()  # en yeni önce
        return entries if limit is None else entries[:limit]

    # -- çalıştırmalar ------------------------------------------------------ #

    def new_run(self) -> tuple[str, Path]:
        run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        folder = self.runs_dir / run_id
        folder.mkdir(parents=True)
        return run_id, folder

    def run_dir(self, run_id: str) -> Path:
        if not _RUN_ID.match(run_id or ""):
            raise NotFound("çalıştırma bulunamadı")
        folder = self.runs_dir / run_id
        if not folder.is_dir():
            raise NotFound("çalıştırma bulunamadı")
        return folder

    def runs(self) -> list[dict[str, Any]]:
        if not self.runs_dir.is_dir():
            return []
        items = []
        for folder in sorted(self.runs_dir.iterdir(), reverse=True):
            if not (folder.is_dir() and _RUN_ID.match(folder.name)):
                continue
            report = read_json(folder / "dwhflow-run.json", {}) or {}
            status = read_json(folder / "durum.json", {}) or {}
            diagrams = report.get("diagrams", [])
            items.append({
                "id": folder.name, "state": status.get("state", "done" if report else "?"),
                "created_at": status.get("created_at") or report.get("created_at"),
                "created_by": status.get("created_by"), "ready": sum(1 for d in diagrams if d.get("html")),
                "total": len(diagrams), "duration_s": report.get("duration_s"),
            })
        return items

    def delete_run(self, run_id: str, *, user: str) -> None:
        folder = self.run_dir(run_id)
        state = (read_json(folder / "durum.json", {}) or {}).get("state")
        if state in ("queued", "running"):
            raise StoreError("çalıştırma sürüyor; bitince silinebilir")
        shutil.rmtree(folder)
        self.log(user, "calistirma_sil", {"calistirma": run_id})

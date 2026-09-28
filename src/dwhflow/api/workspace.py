"""Çalışma alanı: dönüştürme klasörleri ve ayarlar.

Konum varsayılanı ``<cwd>/.dwhflow``::

    .dwhflow/
      config.yaml              ayarlar + LLM yapılandırması (anahtar YOK)
      runs/<id>/inputs/        yüklenen dokümanlar
      runs/<id>/index.html     galeri; diagrams/, model/, dwhflow-run.json

Tasarım gereği kapanan hatalar (bkz. inceleme):

* Hiçbir işlem mevcut bir modelin üzerine yazmaz — her dönüştürme yeni klasör.
* Dosya adları her platformda aynı temizlenir (Windows ``\\`` Linux'ta da ayırıcı),
  yalnız noktalardan oluşan adlar (``..``) reddedilir.
* Yükleme belleğe tamamen okunmadan, boyut sınırıyla diske akıtılır.
* API anahtarı ve parola asla yazılmaz; yalnız ortam değişkeninin adı tutulur.
"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

import yaml

from ..ingest import ACCEPTED
from ..llm import LLMConfig

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_FILES = 50
_CHUNK = 1 << 20

_SAFE = re.compile(r"[^A-Za-z0-9._\- ğüşıöçĞÜŞİÖÇ()]")
_RUN_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-f0-9]{6}$")

DEFAULT_SETTINGS: dict[str, Any] = {
    "quality": "showcase",
    "use_llm_by_default": False,
}


class WorkspaceError(ValueError):
    """Kullanıcı girdisi reddedildi (400)."""


def safe_filename(name: str) -> str:
    """Yol bileşenlerini ve sürpriz karakterleri atar. Her platformda aynı sonuç."""
    base = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _SAFE.sub("_", base).strip().strip(".").strip()
    if not cleaned or set(cleaned) <= {".", "_", " "}:
        raise WorkspaceError(f"geçersiz dosya adı: {name!r}")
    return cleaned[:180]


class Workspace:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.runs = self.root / "runs"
        self.config_file = self.root / "config.yaml"
        self.runs.mkdir(parents=True, exist_ok=True)

    # -- ayarlar ------------------------------------------------------------ #

    def _config(self) -> dict[str, Any]:
        if not self.config_file.is_file():
            return {}
        try:
            return yaml.safe_load(self.config_file.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            return {}

    def _write_config(self, data: dict[str, Any]) -> None:
        self.config_file.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                                    encoding="utf-8")

    def settings(self) -> dict[str, Any]:
        config = self._config()
        return {
            **DEFAULT_SETTINGS,
            **{k: v for k, v in config.get("settings", {}).items() if k in DEFAULT_SETTINGS},
            "llm": self.llm_config().as_dict(),
        }

    def llm_config(self) -> LLMConfig:
        return LLMConfig.from_dict(self._config().get("llm"))

    def save_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        config = self._config()
        llm_patch = patch.get("llm")
        other = {k: v for k, v in patch.items() if k != "llm"}
        unknown = set(other) - set(DEFAULT_SETTINGS)
        if unknown:
            raise WorkspaceError(f"bilinmeyen ayar: {sorted(unknown)}")
        if other:
            config["settings"] = {**config.get("settings", {}), **other}
        if isinstance(llm_patch, dict):
            # Yalnız bilinen alanlar; 'api_key' gibi değerler asla yazılmaz.
            merged = {**self.llm_config().as_dict(),
                      **{k: v for k, v in llm_patch.items() if k in LLMConfig.__dataclass_fields__}}
            config["llm"] = LLMConfig.from_dict(merged).as_dict()
        self._write_config(config)
        return self.settings()

    # -- dönüştürmeler ------------------------------------------------------ #

    def new_run(self) -> tuple[str, Path]:
        run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        folder = self.runs / run_id
        (folder / "inputs").mkdir(parents=True)
        return run_id, folder

    def run_dir(self, run_id: str) -> Path:
        if not _RUN_ID.match(run_id):
            raise WorkspaceError("geçersiz çalışma kimliği")
        folder = self.runs / run_id
        if not folder.is_dir():
            raise FileNotFoundError(run_id)
        return folder

    def store_upload(self, folder: Path, filename: str, stream: BinaryIO) -> Path:
        """Yüklenen dosyayı boyut sınırıyla diske akıtır."""
        name = safe_filename(filename)
        suffix = Path(name).suffix.lower()
        if suffix not in ACCEPTED:
            raise WorkspaceError(f"'{name}': desteklenmeyen uzantı ({suffix or 'yok'})")
        target = folder / "inputs" / name
        size = 0
        with target.open("wb") as handle:
            while chunk := stream.read(_CHUNK):
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    handle.close()
                    target.unlink(missing_ok=True)
                    raise WorkspaceError(f"'{name}' çok büyük (sınır {MAX_FILE_BYTES // 2**20} MB)")
                handle.write(chunk)
        return target

    def write_status(self, folder: Path, status: dict[str, Any]) -> None:
        (folder / "status.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        """Arka plan işçisi dosyayı yazarken okunursa yarım JSON görülebilir; kısa bekleyip
        yeniden dener (Windows'ta açık dosyanın üzerine atomik ``replace`` yapılamıyor)."""
        for attempt in range(5):
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, PermissionError):
                if attempt == 4:
                    raise
                time.sleep(0.05)
        return {}

    def read_status(self, run_id: str) -> dict[str, Any]:
        folder = self.run_dir(run_id)
        status_file = folder / "status.json"
        status = self._read_json(status_file) if status_file.is_file() else {}
        report_file = folder / "dwhflow-run.json"
        if report_file.is_file():
            status["report"] = self._read_json(report_file)
        status["id"] = run_id
        return status

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        items = []
        for folder in sorted(self.runs.iterdir(), reverse=True)[:limit]:
            if not (folder.is_dir() and _RUN_ID.match(folder.name)):
                continue
            try:
                status = self.read_status(folder.name)
            except (OSError, ValueError):
                continue
            report = status.get("report") or {}
            diagrams = report.get("diagrams", [])
            items.append({
                "id": folder.name,
                "title": status.get("title") or report.get("title") or folder.name,
                "state": status.get("state", "?"),
                "created_at": status.get("created_at"),
                "documents": len(status.get("inputs", [])),
                "ready": sum(1 for d in diagrams if d.get("html")),
                "total": len(diagrams),
            })
        return items

    def delete_run(self, run_id: str, *, force: bool = False) -> None:
        folder = self.run_dir(run_id)
        if not force:
            status_file = folder / "status.json"
            state = self._read_json(status_file).get("state") if status_file.is_file() else None
            if state in ("queued", "running"):
                raise WorkspaceError("çalışma sürüyor; bitince silinebilir")
        shutil.rmtree(folder)

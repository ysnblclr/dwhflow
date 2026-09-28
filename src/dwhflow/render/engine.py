"""Archify motor köprüsü.

Desen ``ooiuuii/hermes-archify``'dan alındı:

* Motor **sabitlenmiş** bir sürümdür (``engine/engine.lock.json``): zip pakete
  gömülüdür, ilk kullanımda SHA-256'sı doğrulanıp açılır. Uygulama motoru asla
  kendiliğinden indirmez ya da güncellemez.
* Archify'ın 24 saatte bir attığı güncelleme isteği
  ``ARCHIFY_UPDATE_CHECK_DISABLED=1`` ile her çağrıda kapatılır — araç kapalı
  ağda çalışacak ve dışarıya hiçbir istek atmamalı.
* Bir komutun sıfırdan farklı çıkışı asla "başarılı" raporlanmaz. Chrome yoksa
  Archify çıkış 2 + ``status: skipped`` döner; bu, "tarayıcı kontrolü atlandı"
  diye ayrı bir sonuç olarak taşınır.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ENGINE_DIR = Path(__file__).resolve().parent.parent / "engine"
LOCK_FILE = ENGINE_DIR / "engine.lock.json"
MIN_NODE_MAJOR = 18
FINALIZE_TIMEOUT_S = 240

#: Archify'ın ağ ve ortam davranışını sabitleyen değişkenler.
BASE_ENV = {"ARCHIFY_UPDATE_CHECK_DISABLED": "1"}


class EngineError(RuntimeError):
    """Motor kurulamadı ya da çalıştırılamadı."""


@dataclass
class FinalizeResult:
    """Tek bir ``archify finalize`` çağrısının dürüst özeti."""

    status: str  # pass | skipped | fail | error
    gates: dict[str, str] = field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    html: Path | None = None
    receipt: dict[str, Any] = field(default_factory=dict)
    exit_code: int | None = None
    quality: str = "showcase"
    stderr: str = ""

    @property
    def delivered(self) -> bool:
        """HTML üretildi ve şema/teslim/sıkı kontrol geçti mi."""
        return all(self.gates.get(g) == "pass" for g in ("validate", "deliver", "check"))

    @property
    def browser_verified(self) -> bool:
        return self.gates.get("browser-check") == "pass"

    def summary_tr(self) -> str:
        if self.status == "pass":
            return "tüm kapılar geçti (şema, teslim, sıkı kontrol, tarayıcı)"
        if self.status == "skipped" and self.delivered:
            return "teslim edildi; tarayıcı kontrolü atlandı (Chrome/Chromium bulunamadı)"
        failed = [gate for gate, state in self.gates.items() if state == "fail"]
        if failed:
            return f"başarısız kapı: {', '.join(failed)}"
        return "motor hatası"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "gates": self.gates,
            "quality": self.quality,
            "html": str(self.html) if self.html else None,
            "summary": self.summary_tr(),
            "diagnostics": [
                {"code": d.get("code"), "message": (d.get("message") or "")[:400]}
                for d in self.diagnostics[:20]
            ],
        }


def read_lock() -> dict[str, Any]:
    return json.loads(LOCK_FILE.read_text(encoding="utf-8"))


def default_install_dir() -> Path:
    override = os.environ.get("DWHFLOW_ENGINE_DIR")
    if override:
        return Path(override)
    lock = read_lock()
    return Path.home() / ".dwhflow" / "engine" / f"{lock['name']}-{lock['version']}"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def node_version(node: str | None = None) -> tuple[str | None, int | None]:
    """(yol, ana sürüm). Node yoksa (None, None)."""
    executable = node or shutil.which("node")
    if not executable:
        return None, None
    try:
        output = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=15
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return executable, None
    match = re.match(r"v?(\d+)", output)
    return executable, int(match.group(1)) if match else None


class Engine:
    """Sabitlenmiş Archify motoru."""

    def __init__(self, install_dir: Path | None = None, node: str | None = None) -> None:
        self.lock = read_lock()
        self.install_dir = Path(install_dir) if install_dir else default_install_dir()
        self._node = node

    # -- kurulum ------------------------------------------------------------ #

    @property
    def archive(self) -> Path:
        return ENGINE_DIR / self.lock["file"]

    @property
    def cli(self) -> Path:
        return self.install_dir / "archify" / "bin" / "archify.mjs"

    @property
    def marker(self) -> Path:
        return self.install_dir / ".dwhflow-engine.json"

    def is_installed(self) -> bool:
        if not (self.cli.is_file() and self.marker.is_file()):
            return False
        try:
            return json.loads(self.marker.read_text(encoding="utf-8")).get("sha256") == self.lock["sha256"]
        except (OSError, ValueError):
            return False

    def ensure_installed(self, archive: Path | None = None) -> Path:
        """Gerekirse zip'i doğrulayıp açar. İdempotent ve atomik."""
        if self.is_installed():
            return self.install_dir
        source = Path(archive) if archive else self.archive
        if not source.is_file():
            raise EngineError(f"Archify paketi bulunamadı: {source}")
        actual = sha256_of(source)
        if actual != self.lock["sha256"]:
            raise EngineError(
                f"Archify paketinin SHA-256'sı kilit dosyasıyla uyuşmuyor.\n"
                f"  beklenen: {self.lock['sha256']}\n  bulunan : {actual}\n"
                "Paket bozulmuş ya da değiştirilmiş olabilir; kullanılmadı."
            )
        self.install_dir.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".archify-", dir=self.install_dir.parent))
        try:
            with zipfile.ZipFile(source) as archive_file:
                root = staging.resolve()
                for member in archive_file.infolist():
                    # Önek karşılaştırması yetmez (".archify-x" ile ".archify-x2"); yol ilişkisi denetlenir.
                    if not (staging / member.filename).resolve().is_relative_to(root):
                        raise EngineError(f"zip içinde güvensiz yol: {member.filename}")
                archive_file.extractall(staging)
            (staging / ".dwhflow-engine.json").write_text(
                json.dumps({"version": self.lock["version"], "sha256": self.lock["sha256"]}),
                encoding="utf-8",
            )
            if self.install_dir.exists():
                shutil.rmtree(self.install_dir)
            staging.rename(self.install_dir)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return self.install_dir

    # -- node --------------------------------------------------------------- #

    def node(self) -> str:
        path, major = node_version(self._node)
        if not path:
            raise EngineError(
                "Node.js bulunamadı. Diyagram üretmek için Node.js 18+ gerekli "
                "(https://nodejs.org). Görüntüleme için gerekmez."
            )
        if major is None or major < MIN_NODE_MAJOR:
            raise EngineError(f"Node.js {MIN_NODE_MAJOR}+ gerekli; bulunan: {path} (v{major})")
        return path

    def _env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = {**os.environ, **BASE_ENV}
        if extra:
            env.update(extra)
        return env

    # -- komutlar ----------------------------------------------------------- #

    def doctor(self) -> tuple[bool, str]:
        self.ensure_installed()
        result = subprocess.run(
            [self.node(), str(self.cli), "doctor"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=self._env(), timeout=120,
        )
        return result.returncode == 0, (result.stdout + result.stderr).strip()

    def finalize(
        self,
        diagram_type: str,
        spec_path: Path,
        html_path: Path,
        *,
        quality: str = "showcase",
        env: dict[str, str] | None = None,
    ) -> FinalizeResult:
        """``archify finalize`` çalıştırır. Yollar spec dizinine göre çözülür."""
        self.ensure_installed()
        workdir = spec_path.parent
        command = [
            self.node(), str(self.cli), "finalize", diagram_type,
            spec_path.name, os.path.relpath(html_path, workdir),
            "--quality", quality, "--json",
        ]
        try:
            completed = subprocess.run(
                command, cwd=workdir, capture_output=True, text=True, encoding="utf-8",
                errors="replace", env=self._env(env), timeout=FINALIZE_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return FinalizeResult(status="error", quality=quality,
                                  stderr=f"zaman aşımı ({FINALIZE_TIMEOUT_S} sn)")
        try:
            receipt = json.loads(completed.stdout)
        except ValueError:
            return FinalizeResult(
                status="error", exit_code=completed.returncode, quality=quality,
                stderr=(completed.stderr or completed.stdout)[-2000:],
            )
        status = receipt.get("status") or ("pass" if completed.returncode == 0 else "fail")
        # Sıfırdan farklı çıkış asla "pass" sayılmaz.
        if completed.returncode != 0 and status == "pass":
            status = "fail"
        result = FinalizeResult(
            status=status,
            gates=dict(receipt.get("gates") or {}),
            diagnostics=list(receipt.get("diagnostics") or []),
            receipt=receipt,
            exit_code=completed.returncode,
            quality=quality,
            stderr=completed.stderr[-2000:],
        )
        if result.delivered and html_path.is_file():
            result.html = html_path
        return result

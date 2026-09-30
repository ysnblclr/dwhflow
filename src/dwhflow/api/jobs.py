"""Arka plan işleri: doküman okuma ve diyagram üretimi.

İşler sunucu belleğinde izlenir (son 200 iş). Her iş ilerleme mesajlarını biriktirir;
arayüz ``/api/jobs/{kimlik}`` ile yoklar. Aynı projenin işleri proje kilidiyle sıraya girer.
"""

from __future__ import annotations

import threading
import traceback
import uuid
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

MAX_JOBS = 200
MAX_PROGRESS = 60


class JobRunner:
    def __init__(self, workers: int = 2) -> None:
        self.workers = workers
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dwhflow-is")
        self._closed = False
        self._jobs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def submit(self, kind: str, *, project: str | None, user: str, title: str,
               fn: Callable[[Callable[[str], None]], Any], run: str | None = None) -> dict[str, Any]:
        """``run``: işin ürettiği diyagram çalıştırması (çalıştırma ekranı ilerlemeyi buradan bulur)."""
        job_id = uuid.uuid4().hex[:12]
        job = {"id": job_id, "kind": kind, "project": project, "title": title, "state": "queued",
               "progress": [], "created_by": user, "created_at": _now(), "finished_at": None,
               "result": None, "error": None, "run": run}
        with self._lock:
            self._jobs[job_id] = job
            while len(self._jobs) > MAX_JOBS:
                self._jobs.popitem(last=False)

        def progress(message: str) -> None:
            with self._lock:
                job["state"] = "running"
                job["progress"] = (job["progress"] + [{"at": _now(), "message": message}])[-MAX_PROGRESS:]

        def run() -> None:
            progress("başladı")
            try:
                result = fn(progress)
                with self._lock:
                    job["result"], job["state"] = result, "done"
            except Exception as exc:  # noqa: BLE001 - hata kullanıcıya iş durumunda gösterilir
                with self._lock:
                    job["state"] = "error"
                    job["error"] = f"{type(exc).__name__}: {exc}"
                    job["trace"] = traceback.format_exc(limit=6)
            finally:
                with self._lock:
                    job["finished_at"] = _now()

        with self._lock:
            if self._closed:  # uygulama yeniden başlatıldıysa (ör. testlerde) havuzu yeniden aç
                self.pool = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="dwhflow-is")
                self._closed = False
            self.pool.submit(run)
        return self.get(job_id)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return {k: v for k, v in job.items() if k != "trace"} if job else None

    def list(self, project: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            jobs = [j for j in reversed(self._jobs.values()) if project is None or j["project"] == project]
            return [{k: v for k, v in j.items() if k not in ("trace", "result")} for j in jobs[:limit]]

    def for_run(self, project: str, run: str) -> dict[str, Any] | None:
        """Çalıştırmayı üreten işin son durumu (ilerleme mesajlarıyla); iş bellekten düştüyse ``None``."""
        return next((j for j in self.list(project, limit=MAX_JOBS) if j.get("run") == run), None)

    def active(self, project: str) -> list[dict[str, Any]]:
        return [j for j in self.list(project, limit=MAX_JOBS) if j["state"] in ("queued", "running")]

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            self.pool.shutdown(wait=False, cancel_futures=True)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")

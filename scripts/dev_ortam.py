#!/usr/bin/env python3
"""Yerel geliştirme/deneme ortamı: test yöneticisi + örnek proje içeren bir çalışma alanı kurar.

    python scripts/dev_ortam.py            # ./.dwhflow-dev altında
    dwhflow serve --data-dir .dwhflow-dev/veri

Parola rastgele üretilir ve yalnız ``.dwhflow-dev/giris.txt`` dosyasına yazılır (git'e girmez).
Gerçek kurulumda kullanmayın: yöneticiyi ``dwhflow kullanici ekle`` ile oluşturun.
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dwhflow.auth import UserStore  # noqa: E402
from dwhflow.project import (  # noqa: E402
    ProjectEngine,
    Workspace,
    samples,  # noqa: E402
)


def main() -> int:
    base = ROOT / ".dwhflow-dev"
    workspace = Workspace(base / "veri")
    users = UserStore(workspace.users_file)
    credentials = base / "giris.txt"
    if users.empty():
        password = "Dev-" + secrets.token_urlsafe(12)
        users.create("yonetici", "Deneme Yöneticisi", "yonetici", password)
        credentials.write_text(f"kullanici: yonetici\nparola: {password}\n", encoding="utf-8")
    if not workspace.projects():
        project = workspace.create_project("Kredi Kartı", "Örnek senaryo", user="yonetici")
        for path in samples.scenario_files("kart"):
            with path.open("rb") as handle:
                project.store_upload(path.name, handle, user="yonetici")
        ProjectEngine().build(project, user="yonetici")
    print(f"Çalışma alanı: {workspace.root}")
    print(f"Giriş bilgisi: {credentials}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

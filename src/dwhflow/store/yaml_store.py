"""YAML depo: model dosyalarını oku/yaz (PLAN.md Bölüm 6).

Model, git ile versiyonlanabilen insan-okunur YAML dosyalarında durur. Bir dizin
verildiğinde içindeki tüm ``*.yaml`` / ``*.yml`` dosyaları okunur ve birleştirilir;
hangi bilginin hangi dosyada durduğu serbesttir (kullanıcı istediği gibi böler).

Desteklenen dosya biçimi — her top-level anahtar opsiyonel:

.. code-block:: yaml

    name: demo
    systems:            # liste ya da id->nesne sözlüğü
      - id: sys_dwh
        name: Veri Ambarı
        kind: dwh
    datasets:
      stg_account:      # sözlük biçiminde id anahtardan gelir
        name: STG_ACCOUNT
        kind: staging_table
        layer: staging
    processes: [...]
    edges:
      - kind: writes
        source: job_load_acct
        target: stg_account
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ..model import Dataset, Edge, Model, Process, System

COLLECTIONS: dict[str, type] = {
    "systems": System,
    "datasets": Dataset,
    "processes": Process,
}


class StoreError(Exception):
    """Model dosyaları okunamadı ya da tutarsız."""


def _normalize(raw: Any, key: str, origin: Path) -> list[dict[str, Any]]:
    """Liste ya da sözlük biçimini tek biçime indirger."""
    if raw is None:
        return []
    if isinstance(raw, list):
        items = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                raise StoreError(f"{origin}: {key}[{index}] bir sözlük olmalı")
            items.append(dict(item))
        return items
    if isinstance(raw, dict):
        items = []
        for item_id, item in raw.items():
            if not isinstance(item, dict):
                raise StoreError(f"{origin}: {key}.{item_id} bir sözlük olmalı")
            merged = dict(item)
            merged.setdefault("id", item_id)
            if merged["id"] != item_id:
                raise StoreError(
                    f"{origin}: {key}.{item_id} içindeki id '{merged['id']}' anahtarla uyuşmuyor"
                )
            items.append(merged)
        return items
    raise StoreError(f"{origin}: {key} liste ya da sözlük olmalı, {type(raw).__name__} geldi")


def _yaml_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    # Gizli dizinler (.dwhflow, .git …) modele ait değildir. Önceki sürüm model
    # dizinindeki .dwhflow/config.yaml'ı model sanıp uygulamayı kilitliyordu (inceleme #5).
    files = sorted(
        p for p in path.rglob("*.y*ml")
        if p.is_file() and p.suffix.lower() in (".yaml", ".yml")
        and not any(part.startswith(".") for part in p.relative_to(path).parts)
    )
    if not files:
        raise StoreError(f"{path}: YAML dosyası bulunamadı")
    return files


def load_model(path: str | Path) -> Model:
    """Dizin ya da tek dosyadan modeli yükler."""
    root = Path(path)
    if not root.exists():
        raise StoreError(f"{root}: bulunamadı")

    name: str | None = None
    buckets: dict[str, dict[str, Any]] = {k: {} for k in COLLECTIONS}
    edges: list[Edge] = []

    for file in _yaml_files(root):
        try:
            raw = yaml.safe_load(file.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise StoreError(f"{file}: YAML ayrıştırılamadı — {exc}") from exc
        if raw is None:
            continue
        if not isinstance(raw, dict):
            raise StoreError(f"{file}: dosyanın en üst seviyesi sözlük olmalı")

        unknown = set(raw) - set(COLLECTIONS) - {"edges", "name"}
        if unknown:
            raise StoreError(f"{file}: bilinmeyen anahtar(lar): {sorted(unknown)}")

        if name is None and isinstance(raw.get("name"), str):
            name = raw["name"]

        for key, cls in COLLECTIONS.items():
            for item in _normalize(raw.get(key), key, file):
                item_id = item.get("id")
                if not isinstance(item_id, str):
                    raise StoreError(f"{file}: {key} içinde 'id' eksik ya da metin değil")
                if item_id in buckets[key]:
                    raise StoreError(f"{file}: '{item_id}' id'si birden fazla yerde tanımlı")
                try:
                    buckets[key][item_id] = cls(**item)
                except ValidationError as exc:
                    raise StoreError(f"{file}: {key}.{item_id} geçersiz —\n{exc}") from exc

        for index, item in enumerate(_normalize(raw.get("edges"), "edges", file)):
            try:
                edges.append(Edge(**item))
            except ValidationError as exc:
                raise StoreError(f"{file}: edges[{index}] geçersiz —\n{exc}") from exc

    try:
        return Model(
            name=name or root.stem,
            systems=buckets["systems"],
            datasets=buckets["datasets"],
            processes=buckets["processes"],
            edges=edges,
        )
    except ValidationError as exc:
        raise StoreError(f"{root}: model tutarsız —\n{exc}") from exc


def _dump(data: Any) -> str:
    return yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=100,
    )


def save_model(model: Model, path: str | Path) -> list[Path]:
    """Modeli dizine yazar: systems/datasets/processes/edges ayrı dosyalara.

    ``None`` alanlar ve boş listeler atlanır — dosyalar okunabilir kalsın ve git
    diff'i gürültülü olmasın.
    """
    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def payload(item: Any) -> dict[str, Any]:
        data = item.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
        # Otomatik üretilmiş kenar id'sini yazmayalım: bilgi taşımıyor, diff'i şişirir.
        if isinstance(item, Edge) and data.get("id") == f"{item.kind.value}:{item.source}>{item.target}":
            data.pop("id", None)
        return data

    sections: list[tuple[str, Any]] = [
        ("systems", [payload(v) for v in model.systems.values()]),
        ("datasets", [payload(v) for v in model.datasets.values()]),
        ("processes", [payload(v) for v in model.processes.values()]),
        ("edges", [payload(v) for v in model.edges]),
    ]

    for key, items in sections:
        if not items:
            continue
        file = root / f"{key}.yaml"
        body = {"name": model.name, key: items} if key == "systems" else {key: items}
        file.write_text(_dump(body), encoding="utf-8")
        written.append(file)

    return written

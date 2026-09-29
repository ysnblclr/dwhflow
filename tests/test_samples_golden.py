"""Biçim örnekleri: her örnek dosya tek başına okunur ve altın çıktısıyla karşılaştırılır.

Sözleşme: ``src/dwhflow/examples/ornek_bicimler/README.md``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dwhflow.ingest import ingest_files
from dwhflow.model import EdgeKind
from dwhflow.render.slices import dataset_links

FOLDER = Path(__file__).resolve().parents[1] / "src" / "dwhflow" / "examples" / "ornek_bicimler"
SUFFIX = ".beklenen.json"


def samples() -> list[Path]:
    return sorted(p for p in FOLDER.iterdir()
                  if p.is_file() and not p.name.endswith(SUFFIX) and p.name != "README.md")


def _upper(items) -> set:
    return {tuple(x.upper() for x in item) if isinstance(item, (list, tuple)) else item.upper()
            for item in items}


def test_every_sample_has_expectation():
    missing = [p.name for p in samples() if not (p.parent / (p.name + SUFFIX)).is_file()]
    assert not missing, f"beklenen çıktısı olmayan örnekler: {missing}"


def test_every_expectation_has_sample():
    orphans = [p.name for p in FOLDER.glob(f"*{SUFFIX}") if not (FOLDER / p.name[: -len(SUFFIX)]).is_file()]
    assert not orphans, f"örneği olmayan beklenen dosyaları: {orphans}"


@pytest.mark.parametrize("sample", samples(), ids=lambda p: p.name)
def test_sample_matches_golden(sample: Path):
    expected = json.loads((sample.parent / (sample.name + SUFFIX)).read_text(encoding="utf-8"))
    result = ingest_files([sample])
    model = result.model
    reports = [r for r in result.reports if r.format != "arsiv"]
    assert reports, "rapor yok"
    if "format" in expected:
        assert {r.format for r in reports} == {expected["format"]}, [r.as_dict() for r in reports]
    assert all(r.status == expected.get("status", "ok") for r in reports), [r.as_dict() for r in reports]

    names = {node_id: node.name for node_id, node in model.nodes.items()}
    actual = {
        "datasets": _upper(d.name for d in model.datasets.values()),
        "processes": _upper(p.name for p in model.processes.values()),
        "links": _upper((names[s], names[t]) for (s, t) in dataset_links(model)),
        "precedes": _upper((names[e.source], names[e.target]) for e in model.edges
                           if e.kind is EdgeKind.PRECEDES),
    }
    for key in ("datasets", "processes", "links", "precedes"):
        if key not in expected:
            continue
        wanted = _upper(expected[key])
        if expected.get("subset"):
            assert wanted <= actual[key], f"{key}: eksik {sorted(wanted - actual[key])}"
        else:
            assert actual[key] == wanted, (
                f"{key}: eksik {sorted(wanted - actual[key])}, fazla {sorted(actual[key] - wanted)}")

"""Test yardımcıları."""

from __future__ import annotations

import importlib.util
from functools import cache
from pathlib import Path

import pytest

from dwhflow.model import (
    Column,
    Dataset,
    DatasetKind,
    Edge,
    EdgeKind,
    Layer,
    Model,
    Process,
    ProcessKind,
)
from dwhflow.store import load_model

DEMO_DIR = Path(__file__).resolve().parents[1] / "src" / "dwhflow" / "examples" / "demo_bank_dwh"


@pytest.fixture(scope="session")
def demo_model() -> Model:
    return load_model(DEMO_DIR)


def dataset(
    node_id: str,
    kind: DatasetKind = DatasetKind.STAGING_TABLE,
    layer: Layer = Layer.STAGING,
    **kwargs,
) -> Dataset:
    kwargs.setdefault("name", node_id.upper())
    return Dataset(id=node_id, kind=kind, layer=layer, **kwargs)


def process(
    node_id: str,
    kind: ProcessKind = ProcessKind.SQL_SCRIPT,
    layer: Layer = Layer.STAGING,
    **kwargs,
) -> Process:
    kwargs.setdefault("name", node_id.upper())
    return Process(id=node_id, kind=kind, layer=layer, **kwargs)


def edge(kind: EdgeKind, source: str, target: str, **kwargs) -> Edge:
    return Edge(kind=kind, source=source, target=target, **kwargs)


def build(*items) -> Model:
    """Node/edge listesinden model kurar."""
    model = Model()
    for item in items:
        model.add(item)
    return model


def column(name: str, **kwargs) -> Column:
    return Column(name=name, **kwargs)


def minimal_pipeline() -> Model:
    """``src -> job -> tgt`` şeklinde geçerli en küçük akış."""
    return build(
        dataset("src_t", DatasetKind.SOURCE_OBJECT, Layer.SOURCE),
        process("job_t", ProcessKind.ETL_MAPPING, Layer.STAGING, load_strategy="full"),
        dataset("tgt_t", DatasetKind.STAGING_TABLE, Layer.STAGING),
        edge(EdgeKind.READS, "src_t", "job_t"),
        edge(EdgeKind.WRITES, "job_t", "tgt_t"),
    )


ROOT = Path(__file__).resolve().parents[1]


@cache
def sample_script():
    """``scripts/ornek_dokuman_uret.py`` — Word/PDF üreticileri testlerde de kullanılır."""
    spec = importlib.util.spec_from_file_location("ornek_dokuman_uret",
                                                  ROOT / "scripts" / "ornek_dokuman_uret.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

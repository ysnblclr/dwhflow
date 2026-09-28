"""Kanonik model testleri."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dwhflow.model import (
    Column,
    Dataset,
    DatasetKind,
    EdgeKind,
    Layer,
    Model,
    ProcessKind,
    Status,
    extractor_rank,
    make_ref,
    parse_ref,
)

from .conftest import build, dataset, edge, process


class TestRefs:
    def test_parse_plain(self):
        assert parse_ref("stg_account") == ("stg_account", None)

    def test_parse_column(self):
        assert parse_ref("stg_account#BALANCE") == ("stg_account", "BALANCE")

    def test_make_roundtrip(self):
        ref = make_ref("stg_account", "BALANCE")
        assert ref == "stg_account#BALANCE"
        assert parse_ref(ref) == ("stg_account", "BALANCE")

    def test_make_without_column(self):
        assert make_ref("stg_account") == "stg_account"


class TestIdValidation:
    @pytest.mark.parametrize("bad_id", ["Stg_Account", "stg.account", "stg account", "_stg", "stg#a"])
    def test_rejects_bad_ids(self, bad_id):
        with pytest.raises(ValidationError):
            Dataset(id=bad_id, name="x", kind=DatasetKind.STAGING_TABLE, layer=Layer.STAGING)

    def test_accepts_snake_case(self):
        node = dataset("stg_account_2")
        assert node.id == "stg_account_2"


class TestTypos:
    def test_unknown_field_is_rejected(self):
        """extra='forbid' — YAML'daki yazım hatası sessizce yutulmasın."""
        with pytest.raises(ValidationError):
            Dataset(
                id="t",
                name="T",
                kind=DatasetKind.STAGING_TABLE,
                layer=Layer.STAGING,
                critcality=5,  # kasıtlı yazım hatası
            )

    def test_unknown_enum_value_is_rejected(self):
        with pytest.raises(ValidationError):
            Dataset(id="t", name="T", kind="magic_table", layer=Layer.STAGING)


class TestDataset:
    def test_duplicate_columns_rejected(self):
        with pytest.raises(ValidationError):
            Dataset(
                id="t",
                name="T",
                kind=DatasetKind.STAGING_TABLE,
                layer=Layer.STAGING,
                columns=[Column(name="A"), Column(name="A")],
            )

    def test_column_lookup(self):
        node = dataset("t", columns=[Column(name="A"), Column(name="B")])
        assert node.column("B").name == "B"
        assert node.column("C") is None

    def test_criticality_bounds(self):
        with pytest.raises(ValidationError):
            dataset("t", criticality=6)


class TestEdge:
    def test_auto_id_is_deterministic(self):
        first = edge(EdgeKind.WRITES, "job_a", "tbl_b")
        second = edge(EdgeKind.WRITES, "job_a", "tbl_b")
        assert first.id == second.id == "writes:job_a>tbl_b"

    def test_explicit_id_wins(self):
        assert edge(EdgeKind.WRITES, "a", "b", id="custom").id == "custom"

    def test_node_accessors_strip_columns(self):
        item = edge(EdgeKind.COLUMN_MAPS_TO, "a#X", "b#Y")
        assert (item.source_node, item.target_node) == ("a", "b")

    def test_llm_only_detection(self):
        llm = edge(
            EdgeKind.READS,
            "a",
            "b",
            sources=[{"extractor": "llm:qwen3-8b@extract_edges/v3", "evidence": "cümle"}],
        )
        assert llm.is_llm_only
        assert not edge(EdgeKind.READS, "a", "b").is_llm_only

    def test_mixed_sources_not_llm_only(self):
        item = edge(
            EdgeKind.READS,
            "a",
            "b",
            sources=[{"extractor": "llm:x"}, {"extractor": "sql:sqlglot"}],
        )
        assert not item.is_llm_only


class TestExtractorRank:
    def test_hierarchy_order(self):
        """PLAN.md 5.10: canlı metadata > SQL > ETL > STTM > doküman > LLM."""
        assert extractor_rank("odbc:information_schema") < extractor_rank("sql:sqlglot")
        assert extractor_rank("sql:sqlglot") < extractor_rank("etl:informatica")
        assert extractor_rank("etl:informatica") < extractor_rank("excel_sttm")
        assert extractor_rank("excel_sttm") < extractor_rank("doc")
        assert extractor_rank("doc") < extractor_rank("llm:qwen3-8b")

    def test_unknown_extractor_gets_doc_rank(self):
        assert extractor_rank("something_new") == extractor_rank("doc")

    def test_edge_rank_uses_best_source(self):
        item = edge(
            EdgeKind.READS,
            "a",
            "b",
            sources=[{"extractor": "llm:x"}, {"extractor": "sql:sqlglot"}],
        )
        assert item.best_source_rank == extractor_rank("sql:sqlglot")


class TestModelContainer:
    def test_key_id_mismatch_rejected(self):
        with pytest.raises(ValidationError):
            Model(datasets={"wrong_key": dataset("tbl_a")})

    def test_dataset_process_id_collision_rejected(self):
        with pytest.raises(ValidationError):
            Model(
                datasets={"same": dataset("same")},
                processes={"same": process("same")},
            )

    def test_add_dispatches_by_type(self):
        model = build(dataset("tbl_a"), process("job_a"), edge(EdgeKind.WRITES, "job_a", "tbl_a"))
        assert set(model.datasets) == {"tbl_a"}
        assert set(model.processes) == {"job_a"}
        assert len(model.edges) == 1

    def test_nodes_merges_both_kinds(self):
        model = build(dataset("tbl_a"), process("job_a"))
        assert set(model.nodes) == {"tbl_a", "job_a"}

    def test_node_type_of(self):
        model = build(dataset("tbl_a"), process("job_a"))
        assert model.node_type_of("tbl_a") == "dataset"
        assert model.node_type_of("job_a") == "process"
        assert model.node_type_of("yok") is None

    def test_edges_of_matches_both_ends(self):
        model = build(
            dataset("tbl_a"),
            process("job_a"),
            dataset("tbl_b"),
            edge(EdgeKind.READS, "tbl_a", "job_a"),
            edge(EdgeKind.WRITES, "job_a", "tbl_b"),
        )
        assert len(model.edges_of("job_a")) == 2
        assert len(model.edges_of("tbl_a")) == 1

    def test_stats(self):
        model = build(dataset("tbl_a", columns=[Column(name="X")]), process("job_a"))
        stats = model.stats()
        assert stats["datasets"] == 1
        assert stats["processes"] == 1
        assert stats["columns"] == 1


class TestDefaults:
    def test_status_defaults_to_confirmed(self):
        assert dataset("t").status is Status.CONFIRMED
        assert edge(EdgeKind.WRITES, "a", "b").status is Status.CONFIRMED

    def test_process_defaults(self):
        item = process("job_a", ProcessKind.LOADER_UTILITY)
        assert item.load_strategy is None
        assert item.restartable is None
        assert item.resources == {}

    def test_runtime_best_estimate_prefers_p50(self):
        item = process("job_a", runtime={"avg_min": 10, "p50_min": 7})
        assert item.runtime.best_estimate_min == 7

    def test_runtime_falls_back_to_avg(self):
        item = process("job_a", runtime={"avg_min": 10})
        assert item.runtime.best_estimate_min == 10

    def test_runtime_without_numbers(self):
        item = process("job_a", runtime={})
        assert item.runtime.best_estimate_min is None

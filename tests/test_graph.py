"""Graf motoru testleri."""

from __future__ import annotations

import networkx as nx
import pytest

from dwhflow.graph import ModelGraph
from dwhflow.model import CONTROL_EDGES, DATA_EDGES, EdgeKind

from .conftest import build, dataset, edge, minimal_pipeline, process


@pytest.fixture(scope="module")
def demo_graph(demo_model) -> ModelGraph:
    return ModelGraph(demo_model)


class TestBuild:
    def test_nodes_and_edges_registered(self):
        graph = ModelGraph(minimal_pipeline())
        assert set(graph.flow.nodes) == {"src_t", "job_t", "tgt_t"}
        assert graph.flow.number_of_edges() == 2

    def test_column_edges_kept_separate(self):
        model = build(
            dataset("a", columns=[{"name": "X"}]),
            dataset("b", columns=[{"name": "Y"}]),
            edge(EdgeKind.COLUMN_MAPS_TO, "a#X", "b#Y"),
        )
        graph = ModelGraph(model)
        assert graph.flow.number_of_edges() == 0
        assert graph.columns.number_of_edges() == 1

    def test_unresolved_edge_is_skipped_not_fatal(self):
        """Tek bozuk kenar tüm gezinmeyi düşürmemeli; hatayı doğrulama bildirir."""
        model = build(
            dataset("a"),
            edge(EdgeKind.READS, "a", "job_yok"),
        )
        graph = ModelGraph(model)
        assert graph.flow.number_of_edges() == 0
        assert "a" in graph.flow


class TestTraversal:
    def test_downstream_reaches_end_of_chain(self, demo_graph):
        result = demo_graph.downstream("src_cb_account", kinds=DATA_EDGES)
        assert "mart_risk_daily" in result
        assert "dwn_regulator" in result

    def test_upstream_reaches_source(self, demo_graph):
        result = demo_graph.upstream("mart_risk_daily", kinds=DATA_EDGES)
        assert "src_cb_account" in result
        assert "if_acct_daily" in result

    def test_hops_limit_respected(self, demo_graph):
        one = demo_graph.downstream("stg_account", hops=1, kinds=DATA_EDGES)
        assert one == {"job_ods_account", "job_dq_account", "job_recon_acct"}

    def test_self_excluded(self, demo_graph):
        assert "stg_account" not in demo_graph.downstream("stg_account")

    def test_unknown_node_raises(self, demo_graph):
        with pytest.raises(KeyError):
            demo_graph.downstream("yok_boyle_bir_sey")

    def test_neighborhood_includes_center(self, demo_graph):
        sub = demo_graph.neighborhood("stg_account", hops=1)
        assert "stg_account" in sub
        assert sub.number_of_nodes() > 1

    def test_control_edges_only(self, demo_graph):
        """precedes zinciri veri akışından bağımsız izlenebilmeli."""
        result = demo_graph.downstream("job_cb_extract", kinds=CONTROL_EDGES)
        assert "job_mft_reg" in result
        assert "stg_account" not in result


class TestPaths:
    def test_end_to_end_path_exists(self, demo_graph):
        paths = demo_graph.paths("src_cb_account", "mart_risk_daily")
        assert paths
        shortest = paths[0]
        assert shortest[0] == "src_cb_account"
        assert shortest[-1] == "mart_risk_daily"

    def test_binary_chain_is_in_shortest_path(self, demo_graph):
        shortest = demo_graph.paths("src_cb_account", "mart_risk_daily")[0]
        for step in ("if_acct_daily", "job_mft_acct", "job_decode_acct", "job_load_acct"):
            assert step in shortest

    def test_paths_sorted_by_length(self, demo_graph):
        paths = demo_graph.paths("src_cb_account", "mart_risk_daily")
        assert [len(p) for p in paths] == sorted(len(p) for p in paths)

    def test_no_path_returns_empty(self, demo_graph):
        assert demo_graph.paths("src_crm_customer", "if_acct_daily") == []

    def test_path_edges_match_steps(self, demo_graph):
        path = demo_graph.paths("src_cb_account", "stg_account")[0]
        edges = demo_graph.path_edges(path)
        assert len(edges) == len(path) - 1
        assert edges[0].kind is EdgeKind.READS
        assert edges[-1].kind is EdgeKind.WRITES

    def test_unknown_endpoint_raises(self, demo_graph):
        with pytest.raises(KeyError):
            demo_graph.paths("yok", "stg_account")


class TestWritersReaders:
    def test_writers(self, demo_graph):
        assert demo_graph.writers("stg_account") == ["job_load_acct"]

    def test_produces_file_counts_as_writer(self, demo_graph):
        assert demo_graph.writers("if_acct_daily") == ["job_cb_extract"]

    def test_readers_include_file_consumers(self, demo_graph):
        assert set(demo_graph.readers("if_acct_daily")) == {"job_mft_acct", "job_recon_acct"}

    def test_checks_on(self, demo_graph):
        assert set(demo_graph.checks_on("stg_account")) == {"job_dq_account", "job_recon_acct"}

    def test_checks_exclude_plain_reads(self, demo_graph):
        assert demo_graph.checks_on("stg_customer") == []


class TestBlastRadius:
    def test_mft_failure_reaches_regulator(self, demo_graph):
        radius = demo_graph.blast_radius("job_mft_acct")
        assert "dwn_regulator" in radius["consumption"]
        assert "ex_reg_daily" in radius["consumption"]

    def test_waiting_processes_listed(self, demo_graph):
        radius = demo_graph.blast_radius("job_mft_acct")
        assert "job_load_acct" in radius["processes"]
        assert "job_agg_risk" in radius["processes"]

    def test_partition_is_consistent(self, demo_graph):
        radius = demo_graph.blast_radius("job_load_acct")
        assert radius["datasets"] | radius["processes"] == radius["all"]
        assert radius["consumption"] <= radius["datasets"]

    def test_independent_chain_not_affected(self, demo_graph):
        """CRM zinciri, hesap dosyası zincirinden bağımsız olmalı."""
        radius = demo_graph.blast_radius("job_mft_acct")
        assert "stg_customer" not in radius["all"]

    def test_leaf_failure_has_empty_radius(self, demo_graph):
        assert demo_graph.blast_radius("dwn_regulator")["all"] == set()


class TestColumnLineage:
    def test_upstream_traces_to_source(self, demo_graph):
        result = demo_graph.column_lineage("fact_daily_balance#BALANCE_AMT")
        assert "src_cb_account#BALANCE" in result
        assert "if_acct_daily#BALANCE" in result

    def test_indirect_lineage_included(self, demo_graph):
        """STATUS kolonu BALANCE_AMT'a girmez ama onu etkiler (WHERE STATUS='A')."""
        result = demo_graph.column_lineage("fact_daily_balance#BALANCE_AMT")
        assert "ods_account#STATUS" in result

    def test_downstream_direction(self, demo_graph):
        result = demo_graph.column_lineage("src_cb_account#BALANCE", direction="downstream")
        assert "mart_risk_daily#TOTAL_BALANCE" in result

    def test_unknown_ref_raises(self, demo_graph):
        with pytest.raises(KeyError):
            demo_graph.column_lineage("stg_account#YOK")


class TestStructure:
    def test_demo_has_no_execution_cycle(self, demo_graph):
        assert demo_graph.execution_cycles() == []

    def test_demo_has_no_dataflow_cycle(self, demo_graph):
        assert demo_graph.dataflow_cycles() == []

    def test_execution_order_respects_precedes(self, demo_graph):
        order = demo_graph.execution_order()
        assert order.index("job_cb_extract") < order.index("job_load_acct")
        assert order.index("job_fact_balance") < order.index("job_agg_risk")
        assert order.index("job_agg_risk") < order.index("job_mft_reg")

    def test_execution_order_covers_all_processes(self, demo_graph, demo_model):
        assert set(demo_graph.execution_order()) == set(demo_model.processes)

    def test_cycle_is_detected(self):
        model = build(
            process("job_a"),
            process("job_b"),
            edge(EdgeKind.PRECEDES, "job_a", "job_b"),
            edge(EdgeKind.PRECEDES, "job_b", "job_a"),
        )
        graph = ModelGraph(model)
        assert graph.execution_cycles()
        with pytest.raises(nx.NetworkXUnfeasible):
            graph.execution_order()

    def test_dataflow_cycle_detected_separately(self):
        model = build(
            dataset("tbl_a"),
            process("job_a"),
            edge(EdgeKind.READS, "tbl_a", "job_a"),
            edge(EdgeKind.WRITES, "job_a", "tbl_a"),
        )
        graph = ModelGraph(model)
        assert graph.dataflow_cycles()
        assert graph.execution_cycles() == []


class TestExport:
    def test_dot_contains_nodes_and_edges(self, demo_graph):
        dot = demo_graph.to_dot()
        assert dot.startswith("digraph dwhflow {")
        assert '"stg_account"' in dot
        assert "->" in dot
        assert dot.rstrip().endswith("}")

    def test_dot_scope_filter(self, demo_graph):
        data_only = demo_graph.to_dot(DATA_EDGES)
        assert "precedes" not in data_only

    def test_dot_marks_control_edges_dashed(self, demo_graph):
        dot = demo_graph.to_dot(CONTROL_EDGES)
        assert "style=dashed" in dot

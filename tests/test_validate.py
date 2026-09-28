"""Doğrulama kuralları testleri.

Her kural için: tetikleyen en küçük model + demo modelde tetiklenmediğinin kontrolü.
"""

from __future__ import annotations

from dwhflow.model import DatasetKind, EdgeKind, Layer, ProcessKind, Severity
from dwhflow.validate import summarize, validate

from .conftest import build, dataset, edge, minimal_pipeline, process


def rules_fired(model) -> set[str]:
    return {finding.rule for finding in validate(model)}


def findings_for(model, rule: str) -> list:
    return [f for f in validate(model) if f.rule == rule]


class TestDemoModelIsClean:
    """Demo model fixture olarak kullanılıyor — hata/uyarı üretmemeli."""

    def test_no_errors(self, demo_model):
        counts = summarize(validate(demo_model))
        assert counts["error"] == 0

    def test_no_warnings(self, demo_model):
        findings = validate(demo_model)
        warnings = [f for f in findings if f.severity is Severity.WARNING]
        assert warnings == [], [str(w) for w in warnings]

    def test_only_expected_info_rules(self, demo_model):
        """Kalan INFO'lar kasıtlı: provenance/taslak gösterimi."""
        assert rules_fired(demo_model) == {"draft_pending", "unverified_edge"}

    def test_minimal_pipeline_is_error_free(self):
        assert summarize(validate(minimal_pipeline()))["error"] == 0


class TestReferentialIntegrity:
    def test_unresolved_target(self):
        model = build(dataset("tbl_a"), edge(EdgeKind.DERIVES_FROM, "tbl_a", "yok"))
        assert "unresolved_ref" in rules_fired(model)

    def test_unresolved_source(self):
        model = build(dataset("tbl_a"), edge(EdgeKind.DERIVES_FROM, "yok", "tbl_a"))
        assert "unresolved_ref" in rules_fired(model)

    def test_unknown_column(self):
        model = build(
            dataset("tbl_a", columns=[{"name": "X"}]),
            dataset("tbl_b", columns=[{"name": "Y"}]),
            edge(EdgeKind.COLUMN_MAPS_TO, "tbl_a#YOK", "tbl_b#Y"),
        )
        assert "unknown_column" in rules_fired(model)

    def test_column_ref_on_process_rejected(self):
        model = build(
            process("job_a"),
            dataset("tbl_b", columns=[{"name": "Y"}]),
            edge(EdgeKind.COLUMN_MAPS_TO, "job_a#X", "tbl_b#Y"),
        )
        assert "unresolved_ref" in rules_fired(model)

    def test_columnless_dataset_skips_column_check(self):
        """Şeması bilinmeyen tabloya kolon kenarı kurmak hata değil (henüz bilmiyoruz)."""
        model = build(
            dataset("tbl_a"),
            dataset("tbl_b"),
            edge(EdgeKind.COLUMN_MAPS_TO, "tbl_a#X", "tbl_b#Y"),
        )
        assert "unknown_column" not in rules_fired(model)

    def test_unknown_system(self):
        model = build(dataset("tbl_a", system="sys_yok"))
        assert "unknown_system" in rules_fired(model)


class TestEndpointTypes:
    def test_writes_from_dataset_is_error(self):
        model = build(
            dataset("tbl_a"),
            dataset("tbl_b"),
            edge(EdgeKind.WRITES, "tbl_a", "tbl_b"),
        )
        findings = findings_for(model, "endpoint_type")
        assert findings and findings[0].severity is Severity.ERROR

    def test_reads_into_dataset_is_error(self):
        model = build(
            dataset("tbl_a"),
            dataset("tbl_b"),
            edge(EdgeKind.READS, "tbl_a", "tbl_b"),
        )
        assert "endpoint_type" in rules_fired(model)

    def test_precedes_between_datasets_is_error(self):
        model = build(dataset("tbl_a"), dataset("tbl_b"), edge(EdgeKind.PRECEDES, "tbl_a", "tbl_b"))
        assert "endpoint_type" in rules_fired(model)

    def test_column_edge_without_column_ref_is_error(self):
        model = build(
            dataset("tbl_a", columns=[{"name": "X"}]),
            dataset("tbl_b", columns=[{"name": "Y"}]),
            edge(EdgeKind.COLUMN_MAPS_TO, "tbl_a", "tbl_b"),
        )
        assert "endpoint_type" in rules_fired(model)

    def test_valid_endpoints_produce_nothing(self):
        assert "endpoint_type" not in rules_fired(minimal_pipeline())


class TestFlowIntegrity:
    def test_no_writer(self):
        model = build(dataset("tbl_a", DatasetKind.STAGING_TABLE, Layer.STAGING))
        assert "no_writer" in rules_fired(model)

    def test_source_object_exempt_from_no_writer(self):
        model = build(dataset("src_a", DatasetKind.SOURCE_OBJECT, Layer.SOURCE))
        assert "no_writer" not in rules_fired(model)

    def test_no_reader(self):
        model = minimal_pipeline()
        assert "no_reader" in rules_fired(model)  # tgt_t'yi kimse okumuyor

    def test_control_table_exempt_from_no_reader(self):
        model = build(
            process("job_a"),
            dataset("ctl_a", DatasetKind.CONTROL_TABLE, Layer.STAGING),
            edge(EdgeKind.WRITES, "job_a", "ctl_a"),
        )
        assert "no_reader" not in rules_fired(model)

    def test_process_without_io(self):
        model = build(process("job_a"))
        assert "process_without_io" in rules_fired(model)

    def test_dq_check_without_output_not_flagged(self):
        model = build(
            dataset("tbl_a"),
            process("job_dq", ProcessKind.DQ_CHECK),
            edge(EdgeKind.READS, "tbl_a", "job_dq"),
        )
        assert "process_without_output" not in rules_fired(model)

    def test_layer_direction_backwards(self):
        model = build(
            dataset("mart_a", DatasetKind.MART_TABLE, Layer.MART),
            process("job_a", ProcessKind.SQL_SCRIPT, Layer.STAGING),
            edge(EdgeKind.READS, "mart_a", "job_a"),
        )
        assert "layer_direction" in rules_fired(model)

    def test_check_edges_exempt_from_layer_direction(self):
        """Mutabakat süreci yukarı katmana kenar kurar; bu ters akış değil."""
        model = build(
            dataset("stg_a", DatasetKind.STAGING_TABLE, Layer.STAGING),
            process("job_recon", ProcessKind.RECONCILIATION, Layer.MART),
            edge(EdgeKind.RECONCILES, "job_recon", "stg_a"),
        )
        assert "layer_direction" not in rules_fired(model)

    def test_expected_layer_mismatch(self):
        model = build(dataset("d_a", DatasetKind.DIMENSION_TABLE, Layer.STAGING))
        assert "expected_layer" in rules_fired(model)

    def test_control_table_has_no_expected_layer(self):
        model = build(dataset("ctl_a", DatasetKind.CONTROL_TABLE, Layer.MART))
        assert "expected_layer" not in rules_fired(model)


class TestExecutionAndSchedule:
    def test_execution_cycle_is_error(self):
        model = build(
            process("job_a"),
            process("job_b"),
            edge(EdgeKind.PRECEDES, "job_a", "job_b"),
            edge(EdgeKind.PRECEDES, "job_b", "job_a"),
        )
        findings = findings_for(model, "execution_cycle")
        assert findings and findings[0].severity is Severity.ERROR

    def test_dataflow_cycle_is_warning(self):
        model = build(
            dataset("tbl_a"),
            process("job_a"),
            edge(EdgeKind.READS, "tbl_a", "job_a"),
            edge(EdgeKind.WRITES, "job_a", "tbl_a"),
        )
        findings = findings_for(model, "dataflow_cycle")
        assert findings and findings[0].severity is Severity.WARNING

    def test_successor_starting_earlier_is_flagged(self):
        model = build(
            process("job_a", schedule={"start_times": ["03:00"]}),
            process("job_b", schedule={"start_times": ["01:00"]}),
            edge(EdgeKind.PRECEDES, "job_a", "job_b"),
        )
        assert "schedule_conflict" in rules_fired(model)

    def test_consistent_schedule_not_flagged(self):
        model = build(
            process("job_a", schedule={"start_times": ["01:00"]}),
            process("job_b", schedule={"start_times": ["03:00"]}),
            edge(EdgeKind.PRECEDES, "job_a", "job_b"),
        )
        assert "schedule_conflict" not in rules_fired(model)

    def test_missing_start_times_skips_check(self):
        model = build(
            process("job_a"),
            process("job_b"),
            edge(EdgeKind.PRECEDES, "job_a", "job_b"),
        )
        assert "schedule_conflict" not in rules_fired(model)


class TestSlaChain:
    def test_impossible_sla_is_flagged(self):
        """01:00'da başlayan 180 dakikalık zincir 02:00 SLA'sını tutamaz."""
        model = build(
            process("job_a", schedule={"start_times": ["01:00"]}, runtime={"p50_min": 120}),
            process("job_b", runtime={"p50_min": 60}, sla={"target_time": "02:00"}),
            edge(EdgeKind.PRECEDES, "job_a", "job_b"),
        )
        assert "sla_chain" in rules_fired(model)

    def test_achievable_sla_is_not_flagged(self):
        model = build(
            process("job_a", schedule={"start_times": ["01:00"]}, runtime={"p50_min": 30}),
            process("job_b", runtime={"p50_min": 20}, sla={"target_time": "06:00"}),
            edge(EdgeKind.PRECEDES, "job_a", "job_b"),
        )
        assert "sla_chain" not in rules_fired(model)

    def test_overnight_window_handled(self):
        """23:00'da başlayıp 02:00 SLA'sı olan zincir gece yarısını geçiyor, geçerli."""
        model = build(
            process("job_a", schedule={"start_times": ["23:00"]}, runtime={"p50_min": 30}),
            process("job_b", runtime={"p50_min": 20}, sla={"target_time": "02:00"}),
            edge(EdgeKind.PRECEDES, "job_a", "job_b"),
        )
        assert "sla_chain" not in rules_fired(model)

    def test_sla_fires_when_table_has_dq_check(self):
        """Regresyon (#3): DQ süreci tabloyla reads+validates döngüsü kurar; eski sürüm
        topolojik sıralamada düşüp kuralı DQ kontrolü olan her modelde susturuyordu."""
        model = build(
            process("job_a", schedule={"start_times": ["01:00"]}, runtime={"p50_min": 120}),
            dataset("stg_a"),
            process("dq_a", kind=ProcessKind.DQ_CHECK),
            process("job_b", runtime={"p50_min": 60}, sla={"target_time": "02:00"}),
            edge(EdgeKind.WRITES, "job_a", "stg_a"),
            edge(EdgeKind.READS, "stg_a", "dq_a"),
            edge(EdgeKind.VALIDATES, "dq_a", "stg_a"),
            edge(EdgeKind.READS, "stg_a", "job_b"),
        )
        assert "sla_chain" in rules_fired(model)

    def test_demo_sla_chain_is_feasible(self, demo_model):
        assert "sla_chain" not in rules_fired(demo_model)


class TestOperationalGaps:
    def test_missing_load_strategy(self):
        model = build(
            dataset("tbl_a"),
            process("job_a", ProcessKind.LOADER_UTILITY),
            edge(EdgeKind.WRITES, "job_a", "tbl_a"),
        )
        assert "missing_load_strategy" in rules_fired(model)

    def test_shell_script_not_required_to_have_strategy(self):
        model = build(
            dataset("tbl_a"),
            process("job_a", ProcessKind.SHELL_SCRIPT),
            edge(EdgeKind.WRITES, "job_a", "tbl_a"),
        )
        assert "missing_load_strategy" not in rules_fired(model)

    def test_delta_without_method(self):
        model = build(process("job_a", ProcessKind.SQL_SCRIPT, load_strategy="delta"))
        assert "delta_without_method" in rules_fired(model)

    def test_delta_with_watermark_ok(self):
        model = build(
            process(
                "job_a", ProcessKind.SQL_SCRIPT, load_strategy="delta", watermark_column="LOAD_DT"
            )
        )
        assert "delta_without_method" not in rules_fired(model)

    def test_full_load_needs_no_delta_method(self):
        model = build(process("job_a", ProcessKind.SQL_SCRIPT, load_strategy="full"))
        assert "delta_without_method" not in rules_fired(model)

    def test_critical_without_check(self):
        model = build(
            process("job_a"),
            dataset("tbl_a", criticality=5),
            edge(EdgeKind.WRITES, "job_a", "tbl_a"),
        )
        assert "critical_without_check" in rules_fired(model)

    def test_source_layer_exempt_from_check_rule(self):
        """Kaynak sistem bizim kontrol noktamız değil."""
        model = build(dataset("src_a", DatasetKind.SOURCE_OBJECT, Layer.SOURCE, criticality=5))
        assert "critical_without_check" not in rules_fired(model)

    def test_report_exempt_from_check_rule(self):
        model = build(
            process("job_a", ProcessKind.BATCH_JOB, Layer.CONSUMPTION),
            dataset("rpt_a", DatasetKind.REPORT, Layer.CONSUMPTION, criticality=5),
            edge(EdgeKind.WRITES, "job_a", "rpt_a"),
        )
        assert "critical_without_check" not in rules_fired(model)

    def test_low_criticality_not_flagged(self):
        model = build(
            process("job_a"),
            dataset("tbl_a", criticality=3),
            edge(EdgeKind.WRITES, "job_a", "tbl_a"),
        )
        assert "critical_without_check" not in rules_fired(model)

    def test_file_without_spec(self):
        model = build(dataset("if_a", DatasetKind.INTERFACE_FILE, Layer.INTERFACE))
        assert "file_without_spec" in rules_fired(model)

    def test_file_with_spec_ok(self):
        model = build(
            dataset(
                "if_a",
                DatasetKind.INTERFACE_FILE,
                Layer.INTERFACE,
                file_spec={"format": "fixed_width", "encoding": "cp037"},
            )
        )
        assert "file_without_spec" not in rules_fired(model)

    def test_not_restartable(self):
        model = build(process("job_a", restartable=False))
        assert "not_restartable" in rules_fired(model)

    def test_unknown_restartability_not_flagged(self):
        model = build(process("job_a"))
        assert "not_restartable" not in rules_fired(model)


class TestProvenance:
    def test_draft_node_reported(self):
        model = build(dataset("tbl_a", status="draft"))
        assert "draft_pending" in rules_fired(model)

    def test_draft_edge_reported(self):
        model = build(
            dataset("tbl_a"),
            process("job_a"),
            edge(EdgeKind.WRITES, "job_a", "tbl_a", status="draft"),
        )
        assert "draft_pending" in rules_fired(model)

    def test_llm_edge_is_unverified(self):
        model = build(
            dataset("tbl_a"),
            process("job_a"),
            edge(
                EdgeKind.WRITES,
                "job_a",
                "tbl_a",
                sources=[{"extractor": "llm:qwen3-8b", "evidence": "cümle"}],
            ),
        )
        assert "unverified_edge" in rules_fired(model)

    def test_sql_sourced_edge_is_verified(self):
        model = build(
            dataset("tbl_a"),
            process("job_a"),
            edge(EdgeKind.WRITES, "job_a", "tbl_a", sources=[{"extractor": "sql:sqlglot"}]),
        )
        assert "unverified_edge" not in rules_fired(model)

    def test_llm_without_evidence_is_warning(self):
        model = build(
            dataset("tbl_a"),
            process("job_a"),
            edge(EdgeKind.WRITES, "job_a", "tbl_a", sources=[{"extractor": "llm:qwen3-8b"}]),
        )
        findings = findings_for(model, "llm_without_evidence")
        assert findings and findings[0].severity is Severity.WARNING

    def test_llm_with_evidence_passes(self):
        model = build(
            dataset("tbl_a"),
            process("job_a"),
            edge(
                EdgeKind.WRITES,
                "job_a",
                "tbl_a",
                sources=[{"extractor": "llm:qwen3-8b", "evidence": "X tablosuna yüklenir."}],
            ),
        )
        assert "llm_without_evidence" not in rules_fired(model)


class TestOrdering:
    def test_errors_come_first(self):
        model = build(
            dataset("tbl_a", criticality=5, status="draft"),
            edge(EdgeKind.DERIVES_FROM, "tbl_a", "yok"),
        )
        findings = validate(model)
        severities = [f.severity for f in findings]
        assert severities == sorted(
            severities, key=lambda s: {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}[s]
        )

    def test_summarize_counts(self, demo_model):
        counts = summarize(validate(demo_model))
        assert set(counts) == {"error", "warning", "info"}
        assert sum(counts.values()) == len(validate(demo_model))

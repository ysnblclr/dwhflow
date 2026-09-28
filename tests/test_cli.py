"""CLI testleri — typer'ın CliRunner'ı ile."""

from __future__ import annotations

from typer.testing import CliRunner

from dwhflow.cli import app

from .conftest import DEMO_DIR

runner = CliRunner()
DEMO = str(DEMO_DIR)


class TestInfo:
    def test_runs_on_demo(self):
        result = runner.invoke(app, ["info", DEMO])
        assert result.exit_code == 0
        assert "demo_bank_dwh" in result.output

    def test_defaults_to_demo_model(self):
        result = runner.invoke(app, ["info"])
        assert result.exit_code == 0

    def test_missing_path_exits_2(self):
        result = runner.invoke(app, ["info", "yok_boyle_dizin"])
        assert result.exit_code == 2


class TestValidate:
    def test_demo_passes(self):
        result = runner.invoke(app, ["validate", DEMO])
        assert result.exit_code == 0
        assert "0 hata" in result.output

    def test_severity_filter_hides_info(self):
        result = runner.invoke(app, ["validate", DEMO, "--severity", "warning"])
        assert result.exit_code == 0
        assert "INFO" not in result.output

    def test_bad_severity_exits_2(self):
        result = runner.invoke(app, ["validate", DEMO, "--severity", "kritik"])
        assert result.exit_code == 2

    def test_broken_model_exits_1(self, tmp_path):
        (tmp_path / "m.yaml").write_text(
            "datasets:\n"
            "  tbl_a:\n    name: A\n    kind: staging_table\n    layer: staging\n"
            "edges:\n"
            "  - kind: derives_from\n    source: tbl_a\n    target: yok\n",
            encoding="utf-8",
        )
        result = runner.invoke(app, ["validate", str(tmp_path)])
        assert result.exit_code == 1
        assert "unresolved_ref" in result.output


class TestPaths:
    def test_end_to_end(self):
        result = runner.invoke(
            app, ["paths", DEMO, "--from", "src_cb_account", "--to", "mart_risk_daily"]
        )
        assert result.exit_code == 0
        assert "ACCT_DAILY (EBCDIC)" in result.output
        assert "produces_file" in result.output

    def test_limit_respected(self):
        result = runner.invoke(
            app,
            ["paths", DEMO, "--from", "src_cb_account", "--to", "mart_risk_daily", "--limit", "1"],
        )
        assert result.output.count("Yol 1") == 1
        assert "Yol 2" not in result.output

    def test_no_path_exits_1(self):
        result = runner.invoke(
            app, ["paths", DEMO, "--from", "src_crm_customer", "--to", "if_acct_daily"]
        )
        assert result.exit_code == 1

    def test_unknown_node_exits_2(self):
        result = runner.invoke(app, ["paths", DEMO, "--from", "yok", "--to", "stg_account"])
        assert result.exit_code == 2


class TestShow:
    def test_dataset_card(self):
        result = runner.invoke(app, ["show", "if_acct_daily", DEMO])
        assert result.exit_code == 0
        assert "cp037" in result.output
        assert "ACCTD.cpy" in result.output

    def test_process_card(self):
        result = runner.invoke(app, ["show", "job_load_acct", DEMO])
        assert result.exit_code == 0
        assert "Teradata TPT" in result.output
        assert "truncate_load" in result.output

    def test_unknown_node_exits_2(self):
        result = runner.invoke(app, ["show", "yok", DEMO])
        assert result.exit_code == 2


class TestImpact:
    def test_blast_radius_printed(self):
        result = runner.invoke(app, ["impact", "job_mft_acct", DEMO])
        assert result.exit_code == 0
        assert "Regülatör Bildirimi" in result.output

    def test_unknown_node_exits_2(self):
        result = runner.invoke(app, ["impact", "yok", DEMO])
        assert result.exit_code == 2


class TestOrderAndColumns:
    def test_order_lists_all_jobs(self):
        result = runner.invoke(app, ["order", DEMO])
        assert result.exit_code == 0
        assert "22." in result.output

    def test_columns_upstream(self):
        result = runner.invoke(
            app, ["columns", "fact_daily_balance#BALANCE_AMT", DEMO, "--direction", "upstream"]
        )
        assert result.exit_code == 0
        assert "src_cb_account#BALANCE" in result.output

    def test_columns_unknown_ref_exits_2(self):
        result = runner.invoke(app, ["columns", "stg_account#YOK", DEMO])
        assert result.exit_code == 2


class TestExport:
    def test_dot_to_stdout(self):
        result = runner.invoke(app, ["export-dot", DEMO])
        assert result.exit_code == 0
        assert result.output.startswith("digraph dwhflow {")

    def test_dot_to_file(self, tmp_path):
        out = tmp_path / "g.dot"
        result = runner.invoke(app, ["export-dot", DEMO, "--out", str(out)])
        assert result.exit_code == 0
        assert out.read_text(encoding="utf-8").startswith("digraph")

    def test_bad_scope_exits_2(self):
        result = runner.invoke(app, ["export-dot", DEMO, "--scope", "hepsi"])
        assert result.exit_code == 2

    def test_roundtrip_writes_four_files(self, tmp_path):
        result = runner.invoke(app, ["roundtrip", DEMO, "--out", str(tmp_path / "out")])
        assert result.exit_code == 0
        assert len(list((tmp_path / "out").glob("*.yaml"))) == 4


class TestNames:
    """Dokümandan üretilen modelde kullanıcı kimliği değil adı yazar."""

    def test_paths_by_name(self):
        result = runner.invoke(app, ["paths", DEMO, "--from", "SRC_CB_ACCOUNT", "--to", "MART_RISK_DAILY"])
        assert result.exit_code == 0, result.output
        assert "src_cb_account → mart_risk_daily" in result.output

    def test_show_and_impact_by_name(self):
        assert runner.invoke(app, ["show", "STG_ACCOUNT", DEMO]).exit_code == 0
        assert runner.invoke(app, ["impact", "stg_account", DEMO]).exit_code == 0

    def test_schema_less_name(self, tmp_path):
        out = tmp_path / "cikti"
        assert runner.invoke(app, ["convert", "--ornek", "--no-render", "-o", str(out)]).exit_code == 0
        result = runner.invoke(app, ["show", "F_KART_ISLEM", str(out / "model")])
        assert result.exit_code == 0 and "DWH.F_KART_ISLEM" in result.output


class TestConvert:
    def test_no_render_writes_model_and_gallery(self, tmp_path):
        out = tmp_path / "cikti"
        result = runner.invoke(app, ["convert", "--ornek", "--no-render", "-o", str(out)])
        assert result.exit_code == 0, result.output
        assert (out / "index.html").is_file() and (out / "model").is_dir()
        assert "12" in result.output  # veri nesnesi sayısı özetlenir

    def test_bad_path_argument(self, tmp_path):
        result = runner.invoke(app, ["convert", "--ornek", "--no-render", "-o", str(tmp_path),
                                     "--path", "tek-parca"])
        assert result.exit_code == 2

    def test_missing_input(self, tmp_path):
        result = runner.invoke(app, ["convert", str(tmp_path / "yok.xlsx"), "-o", str(tmp_path / "o")])
        assert result.exit_code == 2

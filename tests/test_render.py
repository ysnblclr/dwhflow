"""Dilimleyici ve Archify derleyicisi (motor çalıştırmadan, spec düzeyinde)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dwhflow.ingest import ingest_files
from dwhflow.model import EdgeKind, Layer, Status
from dwhflow.render.compile import (
    LAYER_TR,
    NODE_MAX_W,
    ORIGIN,
    WIDTH_BUDGET,
    archify_id,
    compile_batch,
    compile_slice,
    translations,
)
from dwhflow.render.slices import (
    DATASET_BUDGET,
    Slice,
    dataset_graph,
    dataset_links,
    plan_default,
    resolve,
    slice_batch,
    slice_focus,
    slice_lineage,
    slice_path,
)

from .conftest import build, dataset, edge, process

SAMPLES = Path(__file__).resolve().parents[1] / "src" / "dwhflow" / "examples" / "ornek_dokumanlar"


@pytest.fixture(scope="module")
def cards_model():
    return ingest_files(sorted(SAMPLES.iterdir())).model


def width(spec):
    layout = spec["layout"]
    return 2 * ORIGIN[0] + layout["cols"] * layout["cellW"] + (layout["cols"] - 1) * layout["gapX"]


class TestLinks:
    def test_processes_collapse_into_table_links(self, demo_model):
        links = dataset_links(demo_model)
        assert ("stg_account", "ods_account") in links
        assert demo_model.processes[links[("stg_account", "ods_account")].processes[0]].name == "SP_LOAD_ODS_ACCOUNT"

    def test_dq_processes_do_not_create_links(self, demo_model):
        links = dataset_links(demo_model)
        for link in links.values():
            assert all(demo_model.processes[p].kind.value not in ("dq_check", "reconciliation")
                       for p in link.processes)

    def test_link_confirmed_if_any_hard_source(self):
        """Regresyon: aynı bağ hem SQL'den hem metinden gelince yanlışlıkla taslak oluyordu."""
        model = build(
            dataset("stg_a"), dataset("ods_a", layer=Layer.ODS), process("job"),
            edge(EdgeKind.READS, "stg_a", "job"), edge(EdgeKind.WRITES, "job", "ods_a"),
            edge(EdgeKind.DERIVES_FROM, "stg_a", "ods_a", status=Status.DRAFT),
        )
        assert dataset_links(model)[("stg_a", "ods_a")].confirmed

    def test_link_draft_if_only_prose(self):
        model = build(dataset("stg_a"), dataset("ods_a", layer=Layer.ODS),
                      edge(EdgeKind.DERIVES_FROM, "stg_a", "ods_a", status=Status.DRAFT))
        assert dataset_links(model)[("stg_a", "ods_a")].draft

    def test_sample_links_corroborated_across_documents(self, cards_model):
        links = dataset_links(cards_model)
        by_name = {(cards_model.datasets[s].name, cards_model.datasets[t].name): link
                   for (s, t), link in links.items()}
        both = by_name[("ODS_KART_ISLEM", "DWH.F_KART_ISLEM")]
        assert len(both.processes) == 2  # SQL ifadesi + STTM job'ı
        drafts = {pair for pair, link in by_name.items() if link.draft}
        assert drafts == {("SRC_KART_ISLEM", "KART_ISLEM_YYYYMMDD.dat"), ("SRC_MUSTERI", "STG_MUSTERI"),
                          ("EXP_BDDK_KART", "BDDK_PORTAL")}


class TestSlices:
    def test_resolve_by_name_and_id(self, demo_model):
        assert resolve(demo_model, "stg_account") == "stg_account"
        assert resolve(demo_model, "STG_ACCOUNT") == "stg_account"
        with pytest.raises(KeyError):
            resolve(demo_model, "YOK_BOYLE")

    def test_path_highlights_shortest_route(self, demo_model):
        graph = dataset_graph(demo_model)
        piece = slice_path(demo_model, graph, "src_cb_account", "dwn_regulator")
        assert piece.highlight[0] == "src_cb_account" and piece.highlight[-1] == "dwn_regulator"
        assert set(piece.highlight) <= set(piece.datasets)

    def test_no_path_raises(self, demo_model):
        with pytest.raises(KeyError, match="yolu yok"):
            slice_path(demo_model, dataset_graph(demo_model), "src_crm_customer", "if_acct_daily")

    def test_long_chain_has_path(self):
        """Regresyon (#4): 20 kenardan uzun zincirde 'yol yok' deniyordu."""
        items = [dataset(f"t{i:02d}") for i in range(30)]
        edges = [edge(EdgeKind.DERIVES_FROM, f"t{i:02d}", f"t{i + 1:02d}") for i in range(29)]
        model = build(*items, *edges)
        piece = slice_path(model, dataset_graph(model), "t00", "t29")
        assert len(piece.highlight) == 30

    def test_lineage_budget_trims_nearest(self):
        items = [dataset(f"t{i:02d}") for i in range(40)] + [dataset("sink")]
        edges = [edge(EdgeKind.DERIVES_FROM, f"t{i:02d}", "sink") for i in range(40)]
        model = build(*items, *edges)
        piece = slice_lineage(model, dataset_graph(model), "sink")
        assert len(piece.datasets) == DATASET_BUDGET and piece.trimmed and "sink" in piece.datasets

    def test_focus_hops(self, demo_model):
        piece = slice_focus(demo_model, dataset_graph(demo_model), "stg_account", hops=1)
        assert "stg_account" in piece.datasets and "fact_daily_balance" not in piece.datasets

    def test_plan_includes_overview_and_batch(self, cards_model):
        kinds = [p.kind for p in plan_default(cards_model)]
        assert kinds[0] == "overview" and "batch" in kinds

    def test_batch_slice_from_scheduler_edges(self, cards_model):
        pieces = slice_batch(cards_model)
        assert pieces and len(pieces[0].processes) >= 8

    def test_large_model_skips_overview_uses_lineage(self):
        items = [dataset(f"t{i:02d}") for i in range(30)] + [dataset("rpt", layer=Layer.CONSUMPTION)]
        edges = [edge(EdgeKind.DERIVES_FROM, f"t{i:02d}", "rpt") for i in range(30)]
        plan = plan_default(build(*items, *edges))
        assert [p.kind for p in plan] == ["lineage"]


class TestCompile:
    def test_spec_shape(self, demo_model):
        piece = Slice(id="x", title="Test", datasets=sorted(demo_model.datasets))
        spec = compile_slice(demo_model, piece, output="x.html")
        assert spec["diagram_type"] == "architecture" and spec["schema_version"] == 1
        assert spec["layout"]["mode"] == "grid" and spec["meta"]["locale"] == "tr"
        assert len(spec["components"]) == len(demo_model.datasets)

    def test_width_budget_respected(self, demo_model):
        """Masaüstünde yazıların ≥6 px görünmesi için genişlik bütçesi aşılmamalı."""
        spec = compile_slice(demo_model, Slice(id="x", title="T", datasets=sorted(demo_model.datasets)),
                             output="x.html")
        assert width(spec) <= WIDTH_BUDGET

    def test_layers_become_rows_in_order(self, demo_model):
        spec = compile_slice(demo_model, Slice(id="x", title="T", datasets=sorted(demo_model.datasets)),
                             output="x.html")
        row = {c["id"]: c["row"] for c in spec["components"]}
        assert row["src_cb_account"] < row["stg_account"] < row["ods_account"] < row["fact_daily_balance"]
        assert [b["label"] for b in spec["boundaries"]] == [LAYER_TR[layer] for layer in Layer]

    def test_components_sized_for_labels(self, demo_model):
        spec = compile_slice(demo_model, Slice(id="x", title="T", datasets=sorted(demo_model.datasets)),
                             output="x.html")
        for component in spec["components"]:
            assert component["size"][0] <= NODE_MAX_W
            assert len(component["label"]) * 6.6 <= component["size"][0] + 6

    def test_parenthetical_name_split(self, demo_model):
        spec = compile_slice(demo_model, Slice(id="x", title="T", datasets=["lnd_acct_daily"]),
                             output="x.html")
        component = spec["components"][0]
        assert component["label"] == "ACCT_DAILY" and component["sublabel"] == "landing, EBCDIC"

    def test_path_emphasis_and_trace(self, demo_model):
        piece = slice_path(demo_model, dataset_graph(demo_model), "stg_account", "fact_daily_balance")
        spec = compile_slice(demo_model, piece, output="x.html")
        assert spec["meta"]["animation"] == "trace"
        assert any(c["variant"] == "emphasis" for c in spec["connections"])

    def test_pii_flows_marked_security(self, demo_model):
        piece = Slice(id="x", title="T", datasets=["src_crm_customer", "stg_customer"])
        spec = compile_slice(demo_model, piece, output="x.html")
        assert spec["connections"][0]["variant"] == "security"

    def test_draft_links_dashed_and_labelled(self):
        model = build(dataset("stg_a"), dataset("ods_a", layer=Layer.ODS),
                      edge(EdgeKind.DERIVES_FROM, "stg_a", "ods_a", status=Status.DRAFT))
        spec = compile_slice(model, Slice(id="x", title="T", datasets=["stg_a", "ods_a"]), output="x.html")
        connection = spec["connections"][0]
        assert connection["variant"] == "dashed" and "taslak" in connection["label"]

    def test_ids_start_with_letter(self):
        assert archify_id("1st_table") == "n1st_table"
        assert archify_id("stg_a") == "stg_a"

    def test_evidence_cards(self, cards_model):
        spec = compile_slice(cards_model, Slice(id="x", title="T", datasets=sorted(cards_model.datasets)),
                             output="x.html")
        titles = [card["title"] for card in spec["cards"]]
        assert "Kaynak dokümanlar" in titles and "Süreçler (kenar etiketleri)" in titles
        evidence = next(c for c in spec["cards"] if c["title"] == "Kaynak dokümanlar")["items"]
        assert any("STTM_Kart_Islemleri.xlsx" in item for item in evidence)

    def test_turkish_translations_embedded(self, demo_model):
        spec = compile_slice(demo_model, Slice(id="x", title="T", datasets=["stg_account"]), output="x.html")
        assert spec["meta"]["translations"]["viewer.export.button"] == "Dışa aktar"

    def test_translation_placeholders_preserved(self):
        """Çeviri yer tutucuları ({label}, {count} …) İngilizce/Almanca kaynakla aynı kalmalı."""
        import re

        for key, value in translations().items():
            assert re.fullmatch(r"[a-z][a-zA-Z0-9]*(\.[a-z][a-zA-Z0-9]*)*", key), key
            assert 1 <= len(value) <= 400

    def test_batch_compile(self, cards_model):
        piece = slice_batch(cards_model)[0]
        spec = compile_batch(cards_model, piece, output="b.html")
        assert len(spec["components"]) == len(piece.processes)
        assert all(c["label"] == "sonra" for c in spec["connections"])
        rows = {c["label"]: c["row"] for c in spec["components"]}
        assert rows["EXT_KART"] < rows["MFT_KART"] < rows["TPT_STG_KART"]
        assert width(spec) <= WIDTH_BUDGET


class TestRepair:
    MESSAGE = ('Label "SP_LOAD_ODS_ACCOUNT" overlaps component "stg_account" — adjust labelDx.\n'
               '  Suggested fix: set labelAt [120, 316] or set labelDy 24 (below); '
               'or set labelAt [120, 236] or set labelDy -56 (above)')

    def spec(self, *connections):
        return {"connections": [dict(c) for c in connections]}

    def test_first_suggestion_applied(self):
        from dwhflow.render.repair import apply_label_fixes

        spec = self.spec({"from": "stg_account", "to": "ods_account", "label": "SP_LOAD_ODS_ACCOUNT"})
        fixed = apply_label_fixes(spec, [{"code": "layout/constraint", "message": self.MESSAGE}])
        assert fixed == 1 and spec["connections"][0]["labelAt"] == [120, 316]

    def test_ambiguous_label_resolved_by_component(self):
        from dwhflow.render.repair import apply_label_fixes

        spec = self.spec({"from": "a", "to": "b", "label": "SP_LOAD_ODS_ACCOUNT"},
                         {"from": "stg_account", "to": "ods_account", "label": "SP_LOAD_ODS_ACCOUNT"})
        apply_label_fixes(spec, [{"code": "layout/constraint", "message": self.MESSAGE}])
        assert "labelAt" not in spec["connections"][0] and "labelAt" in spec["connections"][1]

    def test_no_guessing(self):
        from dwhflow.render.repair import apply_label_fixes

        spec = self.spec({"from": "a", "to": "b", "label": "SP_LOAD_ODS_ACCOUNT"},
                         {"from": "c", "to": "d", "label": "SP_LOAD_ODS_ACCOUNT"})
        assert apply_label_fixes(spec, [{"code": "layout/constraint", "message": self.MESSAGE}]) == 0
        assert apply_label_fixes(spec, [{"code": "schema/invalid", "message": self.MESSAGE}]) == 0
        assert apply_label_fixes(spec, [{"code": "layout/constraint", "message": "başka bir hata"}]) == 0


class TestFindingsCard:
    def card(self, model, datasets, findings):
        spec = compile_slice(model, Slice(id="x", title="T", datasets=datasets), output="x.html",
                             findings=findings)
        return next((c["items"] for c in spec["cards"] if c["title"] == "Doğrulama bulguları"), [])

    def test_turkish_severity_and_named_subject(self, cards_model):
        from dwhflow.validate import validate

        items = self.card(cards_model, sorted(cards_model.datasets), validate(cards_model))
        assert items and all(i.startswith(("Hata · ", "Uyarı · ")) for i in items)
        assert any("KART_ISLEM_YYYYMMDD.dat:" in i for i in items)
        assert not any("WARNING" in i or "ERROR" in i for i in items)

    def test_no_substring_match_and_edge_subjects(self):
        from dwhflow.model import Severity
        from dwhflow.validate.rules import Finding

        model = build(dataset("stg_a"), dataset("stg_ab"), dataset("ods_a", layer=Layer.ODS),
                      edge(EdgeKind.DERIVES_FROM, "stg_a", "ods_a"))
        findings = [
            Finding(rule="r1", severity=Severity.WARNING, message="başka tablo", subject="stg_ab"),
            Finding(rule="r2", severity=Severity.ERROR, message="kenar", subject="derives_from:stg_a>ods_a"),
            Finding(rule="r3", severity=Severity.INFO, message="bilgi", subject="stg_a"),
        ]
        items = self.card(model, ["stg_a", "ods_a"], findings)
        assert items == ["Hata · STG_A → ODS_A: kenar"]

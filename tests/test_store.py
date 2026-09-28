"""YAML depo testleri."""

from __future__ import annotations

import pytest

from dwhflow.model import DatasetKind, EdgeKind, Layer
from dwhflow.store import StoreError, load_model, save_model

from .conftest import DEMO_DIR


def write(tmp_path, name: str, body: str):
    file = tmp_path / name
    file.write_text(body, encoding="utf-8")
    return file


class TestDemoModel:
    def test_loads(self, demo_model):
        assert demo_model.name == "demo_bank_dwh"
        assert len(demo_model.datasets) == 21
        assert len(demo_model.processes) == 22
        assert len(demo_model.edges) == 90

    def test_ebcdic_file_spec_survives(self, demo_model):
        spec = demo_model.datasets["if_acct_daily"].file_spec
        assert spec.encoding == "cp037"
        assert spec.record_length == 350
        assert spec.copybook.endswith("ACCTD.cpy")
        assert spec.has_trailer is True

    def test_all_seven_patterns_present(self, demo_model):
        """PLAN.md 1.2'deki akış desenlerinin izleri modelde olmalı."""
        kinds = {e.kind for e in demo_model.edges}
        assert EdgeKind.CONSUMES_FILE in kinds  # binary dosya zinciri
        assert EdgeKind.PRODUCES_FILE in kinds  # dosya üretimi / export
        assert EdgeKind.PRECEDES in kinds  # batch zinciri
        assert EdgeKind.COLUMN_MAPS_TO in kinds  # kolon lineage
        assert EdgeKind.INFLUENCES in kinds  # indirect lineage
        assert EdgeKind.RECONCILES in kinds  # ABC → Balance
        assert EdgeKind.VALIDATES in kinds  # ABC → Audit

    def test_layers_cover_full_chain(self, demo_model):
        layers = {node.layer for node in demo_model.nodes.values()}
        assert layers == set(Layer)


class TestFormats:
    def test_dict_form_infers_id_from_key(self, tmp_path):
        write(
            tmp_path,
            "m.yaml",
            "datasets:\n  tbl_a:\n    name: A\n    kind: staging_table\n    layer: staging\n",
        )
        model = load_model(tmp_path)
        assert model.datasets["tbl_a"].name == "A"

    def test_list_form_requires_id(self, tmp_path):
        write(
            tmp_path,
            "m.yaml",
            "datasets:\n  - id: tbl_a\n    name: A\n    kind: staging_table\n    layer: staging\n",
        )
        assert load_model(tmp_path).datasets["tbl_a"].id == "tbl_a"

    def test_split_across_files(self, tmp_path):
        write(tmp_path, "a.yaml", "datasets:\n  tbl_a:\n    name: A\n    kind: staging_table\n    layer: staging\n")
        write(tmp_path, "b.yaml", "processes:\n  job_a:\n    name: J\n    kind: sql_script\n    layer: staging\n")
        model = load_model(tmp_path)
        assert set(model.nodes) == {"tbl_a", "job_a"}

    def test_single_file_path(self, tmp_path):
        file = write(
            tmp_path,
            "m.yaml",
            "datasets:\n  tbl_a:\n    name: A\n    kind: staging_table\n    layer: staging\n",
        )
        assert set(load_model(file).datasets) == {"tbl_a"}

    def test_hidden_dirs_and_other_suffixes_ignored(self, tmp_path):
        """Regresyon (#5): .git, .venv gibi gizli klasörlerdeki YAML'lar modele karışıyordu."""
        body = "datasets:\n  tbl_a:\n    name: A\n    kind: staging_table\n    layer: staging\n"
        write(tmp_path, "m.yaml", body)
        (tmp_path / ".git").mkdir()
        write(tmp_path / ".git", "x.yaml", body.replace("tbl_a", "gizli"))
        write(tmp_path, "m.yaml.bak", body.replace("tbl_a", "yedek"))
        assert set(load_model(tmp_path).datasets) == {"tbl_a"}

    def test_empty_file_is_skipped(self, tmp_path):
        write(tmp_path, "empty.yaml", "")
        write(tmp_path, "m.yaml", "datasets:\n  tbl_a:\n    name: A\n    kind: staging_table\n    layer: staging\n")
        assert set(load_model(tmp_path).datasets) == {"tbl_a"}


class TestErrors:
    def test_missing_path(self, tmp_path):
        with pytest.raises(StoreError, match="bulunamadı"):
            load_model(tmp_path / "yok")

    def test_no_yaml_files(self, tmp_path):
        (tmp_path / "readme.txt").write_text("x", encoding="utf-8")
        with pytest.raises(StoreError, match="YAML dosyası bulunamadı"):
            load_model(tmp_path)

    def test_unknown_top_level_key(self, tmp_path):
        write(tmp_path, "m.yaml", "tables:\n  - id: a\n")
        with pytest.raises(StoreError, match="bilinmeyen anahtar"):
            load_model(tmp_path)

    def test_duplicate_id_across_files(self, tmp_path):
        body = "datasets:\n  tbl_a:\n    name: A\n    kind: staging_table\n    layer: staging\n"
        write(tmp_path, "a.yaml", body)
        write(tmp_path, "b.yaml", body)
        with pytest.raises(StoreError, match="birden fazla yerde tanımlı"):
            load_model(tmp_path)

    def test_key_id_conflict(self, tmp_path):
        write(
            tmp_path,
            "m.yaml",
            "datasets:\n  tbl_a:\n    id: tbl_b\n    name: A\n    kind: staging_table\n    layer: staging\n",
        )
        with pytest.raises(StoreError, match="anahtarla uyuşmuyor"):
            load_model(tmp_path)

    def test_invalid_field_names_file_and_item(self, tmp_path):
        write(
            tmp_path,
            "m.yaml",
            "datasets:\n  tbl_a:\n    name: A\n    kind: staging_table\n    layer: staging\n    oops: 1\n",
        )
        with pytest.raises(StoreError) as exc:
            load_model(tmp_path)
        assert "datasets.tbl_a" in str(exc.value)

    def test_broken_yaml(self, tmp_path):
        write(tmp_path, "m.yaml", "datasets: [unclosed\n")
        with pytest.raises(StoreError, match="YAML ayrıştırılamadı"):
            load_model(tmp_path)

    def test_top_level_must_be_mapping(self, tmp_path):
        write(tmp_path, "m.yaml", "- a\n- b\n")
        with pytest.raises(StoreError, match="en üst seviyesi sözlük"):
            load_model(tmp_path)

    def test_invalid_edge_reports_index(self, tmp_path):
        write(tmp_path, "m.yaml", "edges:\n  - kind: nonsense\n    source: a\n    target: b\n")
        with pytest.raises(StoreError, match=r"edges\[0\]"):
            load_model(tmp_path)


class TestRoundtrip:
    def test_save_load_preserves_model(self, demo_model, tmp_path):
        save_model(demo_model, tmp_path)
        reloaded = load_model(tmp_path)
        assert reloaded.stats() == demo_model.stats()
        assert set(reloaded.datasets) == set(demo_model.datasets)
        assert {e.id for e in reloaded.edges} == {e.id for e in demo_model.edges}

    def test_save_preserves_nested_objects(self, demo_model, tmp_path):
        save_model(demo_model, tmp_path)
        reloaded = load_model(tmp_path)
        original = demo_model.processes["job_load_acct"]
        copy = reloaded.processes["job_load_acct"]
        assert copy.load_strategy == original.load_strategy
        assert copy.runtime.p95_min == original.runtime.p95_min
        assert copy.resources == original.resources

    def test_save_preserves_provenance(self, demo_model, tmp_path):
        save_model(demo_model, tmp_path)
        reloaded = load_model(tmp_path)
        llm_edges = [e for e in reloaded.edges if e.is_llm_only]
        assert len(llm_edges) == 1
        assert llm_edges[0].sources[0].evidence

    def test_auto_edge_ids_not_written(self, demo_model, tmp_path):
        save_model(demo_model, tmp_path)
        body = (tmp_path / "edges.yaml").read_text(encoding="utf-8")
        assert "writes:job_load_acct>stg_account" not in body

    def test_save_creates_missing_dir(self, demo_model, tmp_path):
        target = tmp_path / "nested" / "out"
        written = save_model(demo_model, target)
        assert target.exists()
        assert len(written) == 4


class TestDemoPathStable:
    def test_demo_dir_exists(self):
        assert DEMO_DIR.is_dir()
        assert (DEMO_DIR / "datasets.yaml").is_file()

    def test_source_object_kinds(self, demo_model):
        sources = [d for d in demo_model.datasets.values() if d.kind is DatasetKind.SOURCE_OBJECT]
        assert len(sources) == 3

"""Projeler: katmanlı ayarlar, depo, düzeltme katmanı, derleme hattı, onay kuyruğu, şablonlar."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from dwhflow.ingest import ingest_files
from dwhflow.ingest.base import DocReport, ModelBuilder
from dwhflow.ingest.text import read_text
from dwhflow.model import EdgeKind, Layer, Status
from dwhflow.project import CorrectionError, ProjectEngine, StoreError, Workspace, guide, samples
from dwhflow.project import corrections as corr
from dwhflow.project import settings as settings_mod
from dwhflow.project.diff import diff_models
from dwhflow.project.settings import SettingsError
from dwhflow.project.store import NotFound, safe_filename
from dwhflow.render.slices import dataset_links

SAMPLES = Path(__file__).resolve().parents[1] / "src" / "dwhflow" / "examples" / "ornek_dokumanlar"


@pytest.fixture
def workspace(tmp_path) -> Workspace:
    return Workspace(tmp_path / "alan")


@pytest.fixture
def kart(workspace):
    project = workspace.create_project("Kredi Kartı", user="ayse")
    for path in sorted(SAMPLES.iterdir()):
        with path.open("rb") as handle:
            project.store_upload(path.name, handle, user="ayse")
    return project


@pytest.fixture
def engine() -> ProjectEngine:
    return ProjectEngine()


def op(islem, hedef, deger=None, number=1, **extra):
    return corr.Correction(id=f"d-{number:04d}", islem=islem, hedef=hedef, deger=deger or {}, kim="ayse",
                           zaman="2026-09-29T10:00:00", **extra)


def names(model):
    by_id = {i: n.name for i, n in model.nodes.items()}
    return {(by_id[s], by_id[t]) for s, t in dataset_links(model)}


# --------------------------------------------------------------------------- #
# Ayarlar
# --------------------------------------------------------------------------- #


class TestSettings:
    def test_defaults_valid(self):
        settings = settings_mod.effective({}, {})
        assert settings.diyagram.butce == 24 and settings.yz.etkin is False

    def test_layers_and_origins(self):
        result = settings_mod.with_origins({"diyagram": {"butce": 30}}, {"diyagram": {"kalite": "standard"}})
        assert result["values"]["diyagram"]["butce"] == 30
        assert result["origins"]["diyagram.butce"] == "genel"
        assert result["origins"]["diyagram.kalite"] == "proje"
        assert result["origins"]["diyagram.job_butcesi"] == "varsayilan"

    def test_project_overrides_global(self):
        assert settings_mod.effective({"diyagram": {"butce": 30}}, {"diyagram": {"butce": 12}}).diyagram.butce == 12

    def test_patch_none_removes_key(self):
        level = settings_mod.apply_patch({"diyagram": {"butce": 12, "kalite": "standard"}},
                                         {"diyagram": {"butce": None}})
        assert level == {"diyagram": {"kalite": "standard"}}
        assert settings_mod.apply_patch(level, {"diyagram": {"kalite": None}}) == {}

    def test_mapping_leaves_replaced_not_merged(self):
        """Regresyon: okuyucu zorlamasından çıkarılan dosya YAML'da kalıyordu."""
        level = {"okuma": {"okuyucu_zorla": {"a.txt": "jil", "b.txt": "sql"}}}
        patched = settings_mod.apply_patch(level, {"okuma": {"okuyucu_zorla": {"a.txt": "jil"}}})
        assert patched["okuma"]["okuyucu_zorla"] == {"a.txt": "jil"}

    @pytest.mark.parametrize("bad, message", [
        ({"diyagram": {"butce": 3}}, "butce"),
        ({"okuma": {"es_anlamlilar": {"yok": ["x"]}}}, "rol"),
        ({"okuma": {"ad_kurallari": [{"tur": "regex", "deger": "(", "katman": "core"}]}}, "düzenli"),
        ({"okuma": {"okuyucu_zorla": {"a.txt": "olmayan"}}}, "okuyucu"),
        ({"yz": {"profil": "kurum"}}, "profil"),
        ({"yz": {"profiller": {"x": {"api_key_env": "sk-abc123-gizli anahtar"}}}}, "ortam değişkeni"),
        ({"bilinmeyen": 1}, "bilinmeyen"),
    ])
    def test_invalid_rejected(self, bad, message):
        with pytest.raises(SettingsError) as exc:
            settings_mod.effective({}, bad)
        assert message.lower() in str(exc.value).lower() or "extra" in str(exc.value).lower()

    def test_llm_profile_to_config(self):
        settings = settings_mod.effective({"yz": {"etkin": True, "profil": "kurum", "profiller": {
            "kurum": {"base_url": "http://llm.kurum.local/v1/", "model": "qwen3:14b"}}}})
        config = settings.llm_config()
        assert config.enabled and config.model == "qwen3:14b" and config.base_url == "http://llm.kurum.local/v1"

    def test_workspace_rejects_invalid_global(self, workspace):
        with pytest.raises(SettingsError):
            workspace.save_global_settings({"diyagram": {"butce": 1000}})
        assert workspace.global_settings() == {}

    def test_global_change_that_breaks_a_project_rejected(self, workspace):
        """Projenin seçtiği LLM profili genelden silinirse proje ayarı geçersiz kalıyordu."""
        profile = {"kurum": {"base_url": "http://llm.kurum.local/v1", "model": "m"}}
        workspace.save_global_settings({"yz": {"profiller": {**settings_mod.DEFAULTS["yz"]["profiller"], **profile}}})
        project = workspace.create_project("Kart", user="ayse")
        project.save_settings({"yz": {"profil": "kurum"}}, user="ayse")
        with pytest.raises(SettingsError, match="Kart"):
            workspace.save_global_settings({"yz": {"profiller": settings_mod.DEFAULTS["yz"]["profiller"]}})
        assert "kurum" in workspace.global_settings()["yz"]["profiller"]
        assert project.settings().yz.profil == "kurum"
        workspace.save_global_settings({"diyagram": {"butce": 20}})  # bozmayan değişiklik geçer


# --------------------------------------------------------------------------- #
# Depo
# --------------------------------------------------------------------------- #


class TestStore:
    def test_project_slug_unique(self, workspace):
        first = workspace.create_project("Kredi Kartı", user="u")
        second = workspace.create_project("Kredi Kartı", user="u")
        assert (first.slug, second.slug) == ("kredi-karti", "kredi-karti-2")
        assert [p.slug for p in workspace.projects()] == ["kredi-karti", "kredi-karti-2"]

    def test_bad_names(self, workspace):
        with pytest.raises(StoreError):
            workspace.create_project("  ", user="u")
        with pytest.raises(NotFound):
            workspace.project("../x")

    def test_versions(self, workspace):
        project = workspace.create_project("P", user="u")
        first = project.store_upload("sttm.csv", io.BytesIO(b"a;b\n"), user="ayse")
        same = project.store_upload("sttm.csv", io.BytesIO(b"a;b\n"), user="ayse")
        second = project.store_upload("sttm.csv", io.BytesIO(b"a;b\nc;d\n"), user="mehmet")
        assert (first["version"], same["unchanged"], second["version"]) == (1, True, 2)
        document = project.documents()[0]
        assert document["versions"] == 2 and document["uploaded_by"] == "mehmet"
        assert project.document_path("sttm.csv", 1).read_bytes() == b"a;b\n"
        assert project.document_path("sttm.csv").read_bytes() == b"a;b\nc;d\n"

    def test_upload_limits(self, workspace, monkeypatch):
        from dwhflow.project import store

        project = workspace.create_project("P", user="u")
        monkeypatch.setattr(store, "MAX_FILE_BYTES", 10)
        with pytest.raises(StoreError, match="büyük"):
            project.store_upload("x.txt", io.BytesIO(b"x" * 11), user="u")
        with pytest.raises(StoreError, match="desteklenmeyen"):
            project.store_upload("x.exe", io.BytesIO(b"MZ"), user="u", accepted={".txt"})
        assert project.documents() == []
        assert not list(project.docs_dir.rglob(".yukleniyor-*"))

    @pytest.mark.parametrize("raw, expected", [("../../a.txt", "a.txt"), ("C:\\x\\tasarım.docx", "tasarım.docx")])
    def test_safe_filename(self, raw, expected):
        assert safe_filename(raw) == expected

    @pytest.mark.parametrize("raw", ["..", "", "a/.."])
    def test_safe_filename_rejects(self, raw):
        with pytest.raises(StoreError):
            safe_filename(raw)

    def test_audit_newest_first(self, workspace):
        project = workspace.create_project("P", user="u")
        project.log("ayse", "a")
        project.log("mehmet", "b")
        assert [e["islem"] for e in project.audit()] == ["b", "a", "proje_olustur"]
        assert [e["islem"] for e in project.audit(user="ayse")] == ["a"]

    def test_run_ids_validated(self, workspace):
        project = workspace.create_project("P", user="u")
        with pytest.raises(NotFound):
            project.run_dir("../../x")


# --------------------------------------------------------------------------- #
# Düzeltmeler
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def base_model():
    return ingest_files(sorted(SAMPLES.iterdir())).model


class TestCorrections:
    def test_apply_does_not_mutate_input(self, base_model):
        before = base_model.model_dump_json()
        corr.apply(base_model, [op("nesne_sil", {"nesne": "STG_MUSTERI"})])
        assert base_model.model_dump_json() == before

    def test_rename_keeps_id_and_old_name_resolves(self, base_model):
        model, report = corr.apply(base_model, [
            op("yeniden_adlandir", {"nesne": "STG_MUSTERI"}, {"ad": "STG_MUSTERI_YENI"}, 1),
            op("katman_degistir", {"nesne": "STG_MUSTERI"}, {"katman": "ods"}, 2),
        ])
        dataset = model.datasets["stg_musteri"]
        assert dataset.name == "STG_MUSTERI_YENI" and dataset.layer is Layer.ODS
        assert all(r["durum"] == "uygulandi" for r in report.results)

    def test_name_resolution_schema_tolerant(self, base_model):
        model, report = corr.apply(base_model, [op("katman_degistir", {"nesne": "mart_kart_ozet"},
                                                   {"katman": "consumption"})])
        assert report.results[0]["durum"] == "uygulandi"
        assert model.datasets["dwh_mart_kart_ozet"].layer is Layer.CONSUMPTION

    def test_orphan_reported(self, base_model):
        _, report = corr.apply(base_model, [op("katman_degistir", {"nesne": "YOK_BOYLE"}, {"katman": "ods"})])
        assert report.orphans and "bulunamadı" in report.orphans[0]["mesaj"]

    def test_invalid_value_is_error_not_orphan(self, base_model):
        model, report = corr.apply(base_model, [op("katman_degistir", {"nesne": "STG_MUSTERI"}, {"katman": "x"})])
        assert report.results[0]["durum"] == "hata"
        assert model.datasets["stg_musteri"].layer is base_model.datasets["stg_musteri"].layer

    def test_merge_moves_edges_and_aliases(self, base_model):
        model, _ = corr.apply(base_model, [
            op("birlestir", {"nesneler": ["ODS_MUSTERI", "STG_MUSTERI"]}, {"ad": "ODS_MUSTERI"}, 1),
            op("aciklama", {"nesne": "STG_MUSTERI"}, {"aciklama": "birleşti"}, 2),
        ])
        assert "stg_musteri" not in model.datasets
        merged = model.datasets["ods_musteri"]
        assert merged.description == "birleşti" and "STG_MUSTERI" in merged.attrs["birlesen_adlar"]
        assert ("SRC_MUSTERI", "ODS_MUSTERI") in names(model)
        assert all(e.source != e.target for e in model.edges)
        assert len({e.id for e in model.edges}) == len(model.edges)

    def test_merge_dataset_with_process_rejected(self, base_model):
        _, report = corr.apply(base_model, [op("birlestir", {"nesneler": ["STG_MUSTERI", "SP_ODS_KART"]})])
        assert report.results[0]["durum"] == "hata"

    def test_add_link_with_process(self, base_model):
        model, _ = corr.apply(base_model, [op("bag_ekle", {"kaynak": "ODS_MUSTERI", "hedef": "EXP_BDDK_KART"},
                                              {"surec": "YENI_JOB"})])
        assert ("ODS_MUSTERI", "EXP_BDDK_KART") in names(model)
        job = next(p for p in model.processes.values() if p.name == "YENI_JOB")
        assert job.sources[0].extractor == "manual:ayse"

    def test_add_existing_draft_link_confirms(self, base_model):
        assert not next(link for key, link in dataset_links(base_model).items()
                        if key == ("exp_bddk_kart", "bddk_portal")).confirmed
        model, _ = corr.apply(base_model, [op("bag_ekle", {"kaynak": "EXP_BDDK_KART", "hedef": "BDDK_PORTAL",
                                                            "tur": "derives_from"})])
        assert dataset_links(model)[("exp_bddk_kart", "bddk_portal")].confirmed

    def test_delete_and_flip(self, base_model):
        model, report = corr.apply(base_model, [
            op("bag_sil", {"kaynak": "EXP_BDDK_KART", "hedef": "BDDK_PORTAL"}, number=1),
            op("bag_ekle", {"kaynak": "STG_MUSTERI", "hedef": "SRC_MUSTERI"}, number=2),
            op("bag_yon_cevir", {"kaynak": "STG_MUSTERI", "hedef": "SRC_MUSTERI"}, number=3),
        ])
        links = names(model)
        assert ("EXP_BDDK_KART", "BDDK_PORTAL") not in links and ("SRC_MUSTERI", "STG_MUSTERI") in links
        assert ("STG_MUSTERI", "SRC_MUSTERI") not in links
        assert all(r["durum"] == "uygulandi" for r in report.results)

    def test_flip_reads_rejected(self, base_model):
        _, report = corr.apply(base_model, [op("bag_yon_cevir", {"kaynak": "STG_KART_ISLEM", "hedef": "SP_ODS_KART"})])
        assert report.results[0]["durum"] == "sahipsiz"

    def test_assign_process_to_direct_link(self, base_model):
        model, _ = corr.apply(base_model, [op("surec_ata", {"kaynak": "SRC_MUSTERI", "hedef": "STG_MUSTERI"},
                                              {"surec": "TPT_STG_MUSTERI"})])
        link = dataset_links(model)[("src_musteri", "stg_musteri")]
        assert [model.processes[p].name for p in link.processes] == ["TPT_STG_MUSTERI"]
        assert not link.direct_kinds

    def test_column_mapping(self, base_model):
        model, _ = corr.apply(base_model, [op("kolon_esle", {"kaynak": "ODS_MUSTERI#MUSTERI_NO",
                                                              "hedef": "DWH.D_MUSTERI#MUSTERI_KEY"},
                                              {"ifade": "surrogate"})])
        edge = next(e for e in model.edges if e.kind is EdgeKind.COLUMN_MAPS_TO and e.expression == "surrogate")
        assert edge.source == "ods_musteri#MUSTERI_NO"
        assert model.datasets["dwh_d_musteri"].column("MUSTERI_KEY") is not None

    def test_add_node_and_confirm(self, base_model):
        model, _ = corr.apply(base_model, [
            op("nesne_ekle", {"ad": "RPT_RISK_PANO", "tip": "dataset"}, number=1),
            op("onayla", {"nesne": "SRC_KART_ISLEM"}, number=2),
        ])
        report = next(d for d in model.datasets.values() if d.name == "RPT_RISK_PANO")
        assert report.layer is Layer.CONSUMPTION  # ad kuralı RPT_ → tüketim
        assert model.datasets["src_kart_islem"].status is Status.CONFIRMED

    def test_meta_ops_do_not_touch_model(self, base_model):
        model, report = corr.apply(base_model, [op("bulgu_yoksay", {"kural": "x", "ozne": "y"}),
                                                op("oneri_reddet", {"anahtar": "k"}, number=2)])
        assert model.model_dump_json() == base_model.model_dump_json()
        assert all(r["durum"] == "uygulandi" for r in report.results)

    def test_check_raises(self, base_model):
        with pytest.raises(CorrectionError):
            corr.check(base_model, op("nesne_sil", {"nesne": "YOK"}))

    def test_edge_ops_on_names_with_hash(self, base_model):
        """Regresyon: "kart_mart_yukleme #1" TABLO#KOLON sanılıp bağ işlemleri sahipsiz kalıyordu."""
        model, report = corr.apply(base_model, [
            op("onayla", {"kaynak": "ODS_KART_ISLEM", "hedef": "kart_mart_yukleme #1", "tur": "reads"}, number=1),
            op("bag_sil", {"kaynak": "kart_mart_yukleme #2", "hedef": "MART_KART_OZET"}, number=2),
            op("bag_ekle", {"kaynak": "kart_mart_yukleme #1", "hedef": "kart_mart_yukleme #2"}, number=3),
        ])
        assert [r["durum"] for r in report.results] == ["uygulandi"] * 3, report.results
        kinds = {(e.kind, e.source, e.target) for e in model.edges}
        assert (EdgeKind.WRITES, "kart_mart_yukleme_2", "dwh_mart_kart_ozet") not in kinds
        assert (EdgeKind.PRECEDES, "kart_mart_yukleme_1", "kart_mart_yukleme_2") in kinds
        # Gerçek kolon başvurusu yine kolon olarak çözülür.
        model, report = corr.apply(base_model, [op("kolon_esle", {"kaynak": "ODS_MUSTERI#MUSTERI_NO",
                                                                   "hedef": "DWH.D_MUSTERI#YENI"})])
        assert report.results[0]["durum"] == "uygulandi"

    @pytest.mark.parametrize(("hedef", "message"), [
        ({"kaynak": "SP_ODS_KART", "hedef": "ODS_KART_ISLEM", "tur": "reads"}, "kaynak bir veri nesnesi"),
        ({"kaynak": "STG_MUSTERI", "hedef": "ODS_MUSTERI", "tur": "writes"}, "kaynak bir süreç"),
        ({"kaynak": "STG_MUSTERI", "hedef": "SP_ODS_KART", "tur": "precedes"}, "kaynak bir süreç"),
        ({"kaynak": "SP_ODS_KART", "hedef": "STG_MUSTERI", "tur": "derives_from"}, "kaynak bir veri nesnesi"),
        ({"kaynak": "STG_MUSTERI#A", "hedef": "ODS_MUSTERI#B", "tur": "derives_from"}, "kolonlar arasında"),
        ({"kaynak": "STG_MUSTERI", "hedef": "ODS_MUSTERI", "tur": "column_maps_to"}, "TABLO#KOLON"),
    ])
    def test_explicit_edge_kind_checks_endpoints(self, base_model, hedef, message):
        model, report = corr.apply(base_model, [op("bag_ekle", hedef)])
        assert report.results[0]["durum"] == "hata" and message in report.results[0]["mesaj"]
        assert len(model.edges) == len(base_model.edges)

    def test_explicit_edge_kind_valid(self, base_model):
        _, report = corr.apply(base_model, [
            op("bag_ekle", {"kaynak": "STG_MUSTERI", "hedef": "SP_ODS_KART", "tur": "reads"}, number=1),
            op("bag_ekle", {"kaynak": "STG_MUSTERI#A", "hedef": "ODS_MUSTERI#B", "tur": "influences"}, number=2),
        ])
        assert [r["durum"] for r in report.results] == ["uygulandi", "uygulandi"], report.results


# --------------------------------------------------------------------------- #
# Derleme hattı
# --------------------------------------------------------------------------- #


class TestPipeline:
    def test_build_and_state(self, kart, engine):
        state = engine.build(kart, user="ayse")
        summary = state.summary(kart)
        assert summary["stats"]["datasets"] == 12 and summary["documents"]["total"] == 6
        assert summary["review"]["bag"] == 3 and not summary["stale"]
        assert (kart.model_dir / "yaml" / "datasets.yaml").is_file()
        assert kart.audit()[0]["islem"] == "yeniden_oku"

    def test_state_without_build_builds(self, kart, engine):
        assert engine.state(kart).model.datasets

    def test_corrections_survive_rebuild(self, kart, engine):
        engine.build(kart, user="ayse")
        engine.add_corrections(kart, [{"islem": "katman_degistir", "hedef": {"nesne": "STG_MUSTERI"},
                                       "deger": {"katman": "ods"}}], user="ayse")
        fresh = ProjectEngine()  # sunucu yeniden başladı
        state = fresh.build(kart, user="ayse", force=True)
        assert state.model.datasets["stg_musteri"].layer is Layer.ODS
        assert state.applied.results[0]["durum"] == "uygulandi"

    def test_invalid_batch_saves_nothing(self, kart, engine):
        engine.build(kart, user="ayse")
        with pytest.raises(CorrectionError):
            engine.add_corrections(kart, [
                {"islem": "katman_degistir", "hedef": {"nesne": "STG_MUSTERI"}, "deger": {"katman": "ods"}},
                {"islem": "nesne_sil", "hedef": {"nesne": "YOK_BOYLE"}},
            ], user="ayse")
        assert kart.corrections() == []

    def test_undo_redo_and_ids_not_reused(self, kart, engine):
        engine.build(kart, user="ayse")
        first = engine.add_corrections(kart, [{"islem": "aciklama", "hedef": {"nesne": "STG_MUSTERI"},
                                               "deger": {"aciklama": "x"}}], user="ayse")[0]
        engine.undo(kart, first["id"], user="mehmet")
        assert engine.state(kart).model.datasets["stg_musteri"].description is None
        second = engine.add_corrections(kart, [{"islem": "aciklama", "hedef": {"nesne": "ODS_MUSTERI"},
                                                "deger": {"aciklama": "y"}}], user="ayse")[0]
        assert second["id"] != first["id"]
        engine.redo(kart, first["id"], user="mehmet")
        assert engine.state(kart).model.datasets["stg_musteri"].description == "x"
        with pytest.raises(CorrectionError):
            engine.redo(kart, first["id"], user="mehmet")

    def test_orphan_after_document_removed(self, kart, engine):
        engine.build(kart, user="ayse")
        engine.add_corrections(kart, [{"islem": "aciklama", "hedef": {"nesne": "BDDK_PORTAL"},
                                       "deger": {"aciklama": "portal"}}], user="ayse")
        kart.delete_document("BDDK_Bildirim_Sureci.pdf", user="ayse")
        kart.delete_document("Kart_Tasarim_Dokumani.docx", user="ayse")
        state = engine.build(kart, user="ayse")
        assert state.summary(kart)["corrections"]["orphans"] == 1

    def test_stale_after_upload_and_cache_reuse(self, kart, engine):
        first = engine.build(kart, user="ayse")
        kart.store_upload("ek.txt", io.BytesIO("STG_A tablosundan ODS_A tablosuna yüklenir.".encode()), user="ayse")
        assert engine.state(kart).stale
        engine.build(kart, user="ayse")
        kart.delete_document("ek.txt", user="ayse")
        again = engine.build(kart, user="ayse")
        assert again.base.key == first.base.key  # önbellekten

    def test_settings_change_affects_reading(self, kart, engine):
        engine.build(kart, user="ayse")
        kart.save_settings({"okuma": {"ad_kurallari": [{"tur": "onek", "deger": "STG_", "katman": "core"}]}},
                           user="ayse")
        assert engine.state(kart).stale
        state = engine.build(kart, user="ayse")
        assert state.model.datasets["stg_kart_islem"].layer is Layer.CORE

    def test_ignored_findings(self, kart, engine):
        state = engine.build(kart, user="ayse")
        finding = state.findings[0]
        engine.add_corrections(kart, [{"islem": "bulgu_yoksay", "hedef": {"kural": finding.rule,
                                                                         "ozne": finding.subject},
                                       "neden": "biliniyor"}], user="ayse")
        after = engine.state(kart)
        assert len(after.ignored) == 1 and len(after.findings) == len(state.findings) - 1

    def test_preview_does_not_save(self, kart, engine):
        engine.build(kart, user="ayse")
        diff = engine.preview(kart, [{"islem": "nesne_sil", "hedef": {"nesne": "ODS_KART_ISLEM"}}], user="ayse")
        assert any(r["id"] == "ods_kart_islem" for r in diff["nodes_removed"]) and diff["links_removed"]
        assert kart.corrections() == []


class TestReview:
    def test_items_have_stable_keys_and_ops(self, kart, engine):
        state = engine.build(kart, user="ayse")
        keys = {item["key"] for item in state.review}
        assert "bag:EXP_BDDK_KART>BDDK_PORTAL" in keys
        assert keys == {item["key"] for item in ProjectEngine().build(kart, user="ayse", force=True).review}

    def test_accept_and_reject_links(self, kart, engine):
        state = engine.build(kart, user="ayse")
        links = [i for i in state.review if i["type"] == "bag"]
        engine.add_corrections(kart, links[0]["accept_ops"], user="ayse")
        engine.add_corrections(kart, links[1]["reject_ops"], user="ayse")
        after = engine.state(kart)
        remaining = {i["key"] for i in after.review if i["type"] == "bag"}
        assert remaining == {links[2]["key"]}
        by_name = {(after.model.datasets[s].name, after.model.datasets[t].name): link
                   for (s, t), link in dataset_links(after.model).items()}
        assert by_name[(links[0]["source"], links[0]["target"])].confirmed
        assert (links[1]["source"], links[1]["target"]) not in by_name

    def test_rejected_paragraph_not_asked_again(self, kart, engine):
        state = engine.build(kart, user="ayse")
        paragraph = next(i for i in state.review if i["type"] == "paragraf")
        engine.add_corrections(kart, paragraph["reject_ops"], user="ayse")
        assert all(i["key"] != paragraph["key"] for i in ProjectEngine().build(kart, user="ayse").review)

    def test_merge_suggestion(self, workspace, engine):
        project = workspace.create_project("M", user="u")
        text = ("MUSTERI_TBL tablosundan ODS_MUSTERI tablosuna yüklenir.\n\n"
                "SRC_MUSTERI tablosundan STG_MUSTERI tablosuna yüklenir.\n")
        project.store_upload("n.txt", io.BytesIO(text.encode()), user="u")
        state = engine.build(project, user="u")
        # MUSTERI_TBL hiç beslenmediği için kaynak katmanında; üç adaydan aynı katmandaki SRC_MUSTERI.
        merges = [i for i in state.review if i["type"] == "birlestir"]
        assert len(merges) == 1
        merge = merges[0]
        assert set(merge["names"]) == {"MUSTERI_TBL", "SRC_MUSTERI"} and merge["preferred"] == "SRC_MUSTERI"
        engine.add_corrections(project, merge["accept_ops"], user="u")
        assert ("SRC_MUSTERI", "ODS_MUSTERI") in names(engine.state(project).model)

    def test_unrecognized_tables_listed(self, kart, engine):
        state = engine.build(kart, user="ayse")
        table = next(i for i in state.review if i["type"] == "tablo")
        assert table["headers"] and table["roles"]


class TestDiff:
    def test_diff(self, base_model):
        after, _ = corr.apply(base_model, [op("nesne_sil", {"nesne": "BDDK_PORTAL"}, number=1),
                                           op("katman_degistir", {"nesne": "STG_MUSTERI"}, {"katman": "ods"}, 2)])
        diff = diff_models(base_model, after)
        assert [r["id"] for r in diff["nodes_removed"]] == ["bddk_portal"]
        assert {"id": "stg_musteri", "name": "STG_MUSTERI", "type": "dataset", "field": "layer", "old": "staging",
                "new": "ods"} in diff["nodes_changed"]
        assert not diff["empty"] and diff_models(base_model, base_model)["empty"]


class TestTemplatesAndGuide:
    @pytest.mark.parametrize("template_id", sorted(samples.TEMPLATES))
    def test_templates_fully_readable(self, tmp_path, template_id):
        """US-0.5: şablonla yazılan doküman LLM'siz, eksiksiz okunur."""
        name, data = samples.template(template_id)
        path = tmp_path / name
        path.write_bytes(data)
        result = ingest_files([path])
        assert result.reports[0].status == "ok" and not result.reports[0].notes
        assert not result.leftovers and not result.unrecognized_tables
        assert dataset_links(result.model) and result.model.processes

    def test_catalog(self):
        data = samples.catalog()
        assert data["scenarios"][0]["id"] == "kart" and len(data["scenarios"][0]["files"]) == 6
        assert {t["id"] for t in data["templates"]} == set(samples.TEMPLATES)
        assert data["formats"]

    def test_sample_path_traversal(self):
        with pytest.raises(LookupError):
            samples.sample_path("kart", "../ornek_bicimler/README.md")
        with pytest.raises(LookupError):
            samples.sample_path("yok", "x")
        # Dosya listesiyle tanımlı senaryo yalnız listedeki dosyaları verir.
        assert samples.sample_path("etl", "kart_controlm.xml").name == "kart_controlm.xml"
        with pytest.raises(LookupError):
            samples.sample_path("etl", "kart_controlm.json")

    def test_etl_scenario_reads_all_tools(self):
        """Ekibin araçları (DataStage, Control-M, Airflow) tek senaryoda birleşik okunur."""
        result = ingest_files(samples.scenario_files("etl"))
        readers = {r.file: (r.reader, r.status) for r in result.reports}
        assert readers["kart_datastage.dsx"] == ("IBM DataStage", "ok")
        assert readers["kart_controlm.xml"] == ("Control-M XML", "ok")
        assert readers["kart_mart_dag.py"] == ("Apache Airflow DAG", "ok")
        names = {n.name for n in result.model.nodes.values()}
        # Aynı tablo üç araçta da geçer ve tek nesnede birleşir.
        assert {"DWH.F_KART_ISLEM", "DWH.MART_KART_OZET", "SEQ_KART_GECE", "PX_STG_KART"} <= names
        assert sum(1 for d in result.model.datasets.values() if d.name == "DWH.F_KART_ISLEM") == 1

    @pytest.mark.parametrize("pattern", guide.SENTENCE_PATTERNS, ids=lambda p: p["example"][:30])
    def test_guide_examples_are_read(self, pattern):
        """Rehberdeki her örnek cümle gerçekten okunur (rehber yalan söylemez)."""
        builder, report = ModelBuilder(), DocReport("x", "t")
        assert read_text(pattern["example"], builder, report, file="x").flows >= 1

    def test_guide_reflects_settings(self):
        config = settings_mod.effective({}, {"okuma": {"es_anlamlilar": {"target_table": ["Hdf Tbl"]}}}).ingest_config()
        data = guide.build(config)
        target = next(r for r in data["roles"] if r["role"] == "target_table")
        assert {"text": "hdf tbl", "custom": True} in target["synonyms"]
        assert "**hdf tbl**" in guide.to_markdown(data)


def test_apostrophe_job_words():
    """Regresyon: "X job'ı ile" / "job'ıyla" yazımında job adı yakalanmıyordu."""
    for sentence in ("SRC_A tablosundan STG_A tablosuna TPT_A job'ı ile yüklenir.",
                     "SRC_A tablosundan STG_A tablosuna TPT_A job'ıyla yüklenir."):
        builder = ModelBuilder()
        read_text(sentence, builder, DocReport("x", "t"), file="x")
        assert [p.name for p in builder.build().processes.values()] == ["TPT_A"], sentence

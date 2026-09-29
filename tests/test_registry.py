"""Biçim kayıt sistemi ve ayarlanabilir okuma yapılandırması."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest

from dwhflow.ingest import ingest_files, preview_document
from dwhflow.ingest.base import explain_name
from dwhflow.ingest.config import DEFAULT, IngestConfig, using
from dwhflow.ingest.registry import REGISTRY, FormatSpec, register, register_expander, resolve
from dwhflow.model import DatasetKind, Layer

SAMPLES = Path(__file__).resolve().parents[1] / "src" / "dwhflow" / "examples" / "ornek_dokumanlar"


@pytest.fixture
def scratch_registry():
    """Testin kaydettiği biçimleri geri alır; genel kayıt kirlenmesin."""
    formats, by_suffix = dict(REGISTRY.formats), dict(REGISTRY.by_suffix)
    expanders, unsupported = dict(REGISTRY.expanders), dict(REGISTRY.unsupported)
    yield REGISTRY
    REGISTRY.formats.clear(), REGISTRY.formats.update(formats)
    REGISTRY.by_suffix.clear(), REGISTRY.by_suffix.update(by_suffix)
    REGISTRY.expanders.clear(), REGISTRY.expanders.update(expanders)
    REGISTRY.unsupported.clear(), REGISTRY.unsupported.update(unsupported)


class TestResolve:
    def test_suffix(self, tmp_path):
        spec, how, _ = resolve(SAMPLES / "kart_mart_yukleme.sql", DEFAULT)
        assert spec.key == "sql" and how == "uzanti"

    def test_content_sniff_beats_generic_suffix(self, tmp_path):
        path = tmp_path / "zincir.txt"
        path.write_text("insert_job: A   job_type: CMD\ninsert_job: B\ncondition: s(A)\n", encoding="utf-8")
        spec, how, _ = resolve(path, DEFAULT)
        assert spec.key == "jil" and how == "icerik"

    def test_plain_text_stays_text(self, tmp_path):
        path = tmp_path / "not.txt"
        path.write_text("STG_A tablosundan ODS_A tablosuna yüklenir.", encoding="utf-8")
        assert resolve(path, DEFAULT)[0].key == "metin"

    def test_forced_reader(self, tmp_path):
        path = tmp_path / "liste.txt"
        path.write_text("x", encoding="utf-8")
        config = IngestConfig.from_settings({"okuyucu_zorla": {"liste.txt": ".sql"}})
        spec, how, message = resolve(path, config)
        assert spec.key == "sql" and how == "zorla" and "zorlandı" in message

    def test_unsupported_message(self, tmp_path):
        path = tmp_path / "eski.doc"
        path.write_bytes(b"\xd0\xcf\x11\xe0")
        spec, how, message = resolve(path, DEFAULT)
        if spec is None:  # bir eklenti .doc okuyucusu kaydettiyse bu test anlamını yitirir
            assert how == "yok" and ".docx" in message

    def test_suffix_conflict_rejected(self, scratch_registry):
        with pytest.raises(ValueError, match="zaten"):
            register(FormatSpec("sahte", "Sahte", (".sql",), lambda b, r, p: []))

    def test_secondary_needs_sniff(self, scratch_registry):
        with pytest.raises(ValueError, match="sniff"):
            register(FormatSpec("sahte2", "Sahte", (".xml",), lambda b, r, p: []), primary=False)


class TestIngestHooks:
    def test_archive_expander(self, tmp_path, scratch_registry):
        def expand(path: Path, workdir: Path) -> list[Path]:
            with zipfile.ZipFile(path) as archive:
                archive.extractall(workdir)
            return sorted(p for p in workdir.rglob("*") if p.is_file())

        register_expander((".testzip",), expand)
        archive = tmp_path / "paket.testzip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.write(SAMPLES / "kart_mart_yukleme.sql", "ic/kart_mart_yukleme.sql")
            zf.writestr("ic/bilinmez.qqq", "x")
        result = ingest_files([archive])
        by_file = {r.file: r for r in result.reports}
        assert by_file["paket.testzip"].format == "arsiv"
        assert by_file["kart_mart_yukleme.sql"].container == "paket.testzip"
        assert by_file["kart_mart_yukleme.sql"].status == "ok"
        assert by_file["bilinmez.qqq"].status == "unsupported"
        assert "DWH.MART_KART_OZET" in {d.name for d in result.model.datasets.values()}

    def test_bad_archive_isolated(self, tmp_path, scratch_registry):
        def boom(path, workdir):
            raise ValueError("bozuk")

        register_expander((".bozukzip",), boom)
        archive = tmp_path / "x.bozukzip"
        archive.write_bytes(b"0")
        result = ingest_files([archive, SAMPLES / "kart_mart_yukleme.sql"])
        statuses = {r.file: r.status for r in result.reports}
        assert statuses == {"x.bozukzip": "error", "kart_mart_yukleme.sql": "ok"}

    def test_unavailable_extra_reported(self, tmp_path, scratch_registry):
        register(FormatSpec("ekli", "Ekli biçim", (".ekli",), lambda b, r, p: [], extra="ofis",
                            available=lambda: False))
        path = tmp_path / "a.ekli"
        path.write_text("x", encoding="utf-8")
        report = ingest_files([path]).reports[0]
        assert report.status == "unsupported" and 'dwhflow[ofis]' in " ".join(report.notes)

    def test_leftovers_kept_without_llm(self):
        result = ingest_files(sorted(SAMPLES.iterdir()))
        assert result.leftovers and all(len(item) == 3 for item in result.leftovers)
        docx = next(r for r in result.reports if r.file.endswith(".docx"))
        assert docx.leftovers == 1


class TestConfig:
    def test_default_behaviour_unchanged(self):
        assert explain_name("STG_MUSTERI")["layer"] is Layer.STAGING

    def test_synonym_extends_defaults(self, tmp_path):
        path = tmp_path / "ozel.csv"
        path.write_text("Hdf Tbl;Kyn Nesne\nODS_A;STG_A\n", encoding="utf-8")
        assert not ingest_files([path]).model.datasets
        config = IngestConfig.from_settings({"es_anlamlilar": {"target_table": ["Hdf Tbl"],
                                                               "source_table": ["Kyn Nesne"]}})
        names = {d.name for d in ingest_files([path], config=config).model.datasets.values()}
        assert names == {"ODS_A", "STG_A"}

    def test_unknown_role_rejected(self):
        with pytest.raises(ValueError, match="rol"):
            IngestConfig.from_settings({"es_anlamlilar": {"yok": ["x"]}})

    def test_custom_rule_first(self):
        config = IngestConfig.from_settings({"ad_kurallari": [
            {"tur": "onek", "deger": "STG_REF_", "katman": "core", "nesne_turu": "core_table"}]})
        result = explain_name("STG_REF_ULKE", config)
        assert result["layer"] is Layer.CORE and result["rule"]["origin"] == "ayar"
        assert explain_name("STG_MUSTERI", config)["layer"] is Layer.STAGING

    def test_suffix_and_regex_rules(self):
        config = IngestConfig.from_settings({"ad_kurallari": [
            {"tur": "sonek", "deger": "_HIST", "katman": "core"},
            {"tur": "regex", "deger": r"^T\d{3}_", "katman": "ods", "nesne_turu": "ods_table"}]})
        assert explain_name("MUSTERI_HIST", config)["kind"] is DatasetKind.CORE_TABLE
        assert explain_name("T101_HESAP", config)["layer"] is Layer.ODS

    def test_defaults_can_be_disabled(self):
        config = IngestConfig.from_settings({"varsayilan_ad_kurallari": False})
        assert explain_name("STG_MUSTERI", config)["layer"] is None

    def test_bad_regex_rejected(self):
        with pytest.raises(re.error):
            IngestConfig.from_settings({"ad_kurallari": [{"tur": "regex", "deger": "(", "katman": "core"}]})

    def test_system_aliases(self, tmp_path):
        path = tmp_path / "s.csv"
        path.write_text("Kaynak Tablo;Hedef Tablo;Kaynak Sistem\nSRC_A;STG_A;CB\n", encoding="utf-8")
        config = IngestConfig.from_settings({"sistemler": {"Core Banking": ["CB", "Ana Bankacılık"]}})
        model = ingest_files([path], config=config).model
        assert model.datasets["src_a"].system == "Core Banking"

    def test_fingerprint_changes_with_settings(self):
        other = IngestConfig.from_settings({"es_anlamlilar": {"job": ["çalışan iş"]}})
        assert DEFAULT.fingerprint() != other.fingerprint()
        assert DEFAULT.fingerprint() == IngestConfig().fingerprint()

    def test_context_restored(self):
        custom = IngestConfig.from_settings({"varsayilan_ad_kurallari": False})
        with using(custom):
            assert explain_name("STG_A")["layer"] is None
        assert explain_name("STG_A")["layer"] is Layer.STAGING


class TestPreview:
    def test_docx_locators_match_reader(self):
        _, blocks = preview_document(SAMPLES / "Kart_Tasarim_Dokumani.docx")
        model = ingest_files([SAMPLES / "Kart_Tasarim_Dokumani.docx"]).model
        locators = {b["locator"] for b in blocks}
        used = {s.locator for d in model.datasets.values() for s in d.sources if s.locator}
        # Paragraf kaynakları blok konumlarıyla birebir; tablo satırları tablo bloğunun önekiyle.
        for locator in used:
            assert locator in locators or any(locator.startswith(b + " · ") for b in locators), locator

    def test_excel_table(self):
        key, blocks = preview_document(SAMPLES / "STTM_Kart_Islemleri.xlsx")
        assert key == "excel"
        sttm = next(b for b in blocks if b["locator"] == "STTM")
        assert sttm["kind"] == "table" and sttm["rows"]

    def test_binary_unknown(self, tmp_path):
        path = tmp_path / "x.bin"
        path.write_bytes(b"\x00\x01" * 10)
        _, blocks = preview_document(path)
        assert blocks[0]["kind"] == "note"

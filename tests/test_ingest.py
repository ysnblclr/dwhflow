"""Doküman okuyucuları: ad çözümleme, tablo yorumlama, metin kuralları, dosya türleri."""

from __future__ import annotations

import zipfile
from pathlib import Path

import openpyxl
import pytest

from dwhflow.ingest import ingest_files
from dwhflow.ingest.base import (
    DocReport,
    ModelBuilder,
    bare_key,
    infer_layer_kind,
    name_key,
    parse_load_strategy,
)
from dwhflow.ingest.tables import TableView, _match_roles, interpret
from dwhflow.ingest.text import extract_sentence, looks_like_table, split_markdown
from dwhflow.model import DatasetKind, Layer, LoadStrategy, Status

from .conftest import sample_script

SAMPLES = Path(__file__).resolve().parents[1] / "src" / "dwhflow" / "examples" / "ornek_dokumanlar"


def names(model):
    return {d.name for d in model.datasets.values()}


def by_name(model, name):
    return next(d for d in model.datasets.values() if d.name == name)


# --------------------------------------------------------------------------- #
# Ad çözümleme ve katman tahmini
# --------------------------------------------------------------------------- #


class TestNames:
    def test_brackets_and_quotes_removed(self):
        assert name_key('[dbo].["Stg_Account"]') == "DBO.STG_ACCOUNT"

    def test_bare_key_strips_schema(self):
        assert bare_key("DWH.F_SALES") == "F_SALES"

    def test_file_extension_is_not_schema(self):
        """Regresyon: ``.dat`` şema ayırıcısı sanılıp dosya staging tablosu olmuştu."""
        assert bare_key("KART_ISLEM.DAT") == "KART_ISLEM.DAT"
        assert infer_layer_kind("KART_ISLEM_YYYYMMDD.dat") == (Layer.INTERFACE, DatasetKind.INTERFACE_FILE)

    @pytest.mark.parametrize("name,layer,kind", [
        ("STG_ACCOUNT", Layer.STAGING, DatasetKind.STAGING_TABLE),
        ("ODS_CUSTOMER", Layer.ODS, DatasetKind.ODS_TABLE),
        ("DWH.D_CUSTOMER", Layer.MART, DatasetKind.DIMENSION_TABLE),
        ("F_SALES", Layer.MART, DatasetKind.FACT_TABLE),
        ("RPT_RISK", Layer.CONSUMPTION, DatasetKind.REPORT),
        ("CTL_WATERMARK", Layer.STAGING, DatasetKind.CONTROL_TABLE),
    ])
    def test_prefix_inference(self, name, layer, kind):
        assert infer_layer_kind(name) == (layer, kind)

    def test_unknown_name_left_to_topology(self):
        assert infer_layer_kind("MUSTERI_OZET") == (None, None)

    @pytest.mark.parametrize("text,expected", [
        ("SCD2", LoadStrategy.SCD2), ("Tam yükleme", LoadStrategy.FULL),
        ("delta", LoadStrategy.DELTA), ("MERGE / upsert", LoadStrategy.MERGE),
        ("truncate", LoadStrategy.TRUNCATE_LOAD), ("artımlı", LoadStrategy.DELTA),
        ("", None), ("bilinmeyen", None),
    ])
    def test_load_strategy(self, text, expected):
        assert parse_load_strategy(text) is expected


class TestBuilder:
    def test_schema_alias_merge(self):
        """``F_SALES`` ile ``DWH.F_SALES`` tek bir şemalı karşılık varsa birleşir."""
        builder = ModelBuilder()
        a = builder.dataset("STG_SALES")
        b = builder.dataset("F_SALES")
        c = builder.dataset("DWH.F_SALES")
        builder.flow(a, b)
        builder.flow(c, builder.dataset("MART_X"))
        model = builder.build()
        assert "F_SALES" not in names(model)
        assert "DWH.F_SALES" in names(model)
        assert len(model.edges) == 2

    def test_ambiguous_alias_not_merged(self):
        builder = ModelBuilder()
        builder.dataset("F_SALES")
        builder.dataset("A.F_SALES")
        builder.dataset("B.F_SALES")
        assert {"F_SALES", "A.F_SALES", "B.F_SALES"} <= names(builder.build())

    def test_topology_layers_for_unprefixed_names(self):
        builder = ModelBuilder()
        builder.flow(builder.dataset("MUSTERI"), builder.dataset("MUSTERI_OZET"))
        model = builder.build()
        assert by_name(model, "MUSTERI").layer is Layer.SOURCE
        assert by_name(model, "MUSTERI_OZET").layer is not Layer.SOURCE

    def test_draft_becomes_confirmed_when_a_hard_source_agrees(self):
        builder = ModelBuilder()
        a, b = builder.dataset("STG_A", draft=True), builder.dataset("ODS_A", draft=True)
        builder.flow(a, b, draft=True)
        builder.dataset("STG_A")
        builder.flow(a, b, draft=False)
        model = builder.build()
        assert by_name(model, "STG_A").status is Status.CONFIRMED
        assert all(e.status is Status.CONFIRMED for e in model.edges)

    def test_prose_only_facts_stay_draft(self):
        builder = ModelBuilder()
        builder.flow(builder.dataset("STG_A", draft=True), builder.dataset("ODS_A", draft=True), draft=True)
        model = builder.build()
        assert all(d.status is Status.DRAFT for d in model.datasets.values())

    def test_via_process_creates_reads_and_writes(self):
        builder = ModelBuilder()
        job = builder.process("LOAD_ODS_A")
        builder.flow(builder.dataset("STG_A"), builder.dataset("ODS_A"), via=job)
        model = builder.build()
        kinds = sorted(e.kind.value for e in model.edges)
        assert kinds == ["reads", "writes"]
        assert next(iter(model.processes.values())).layer is Layer.ODS


# --------------------------------------------------------------------------- #
# Tablo yorumlama
# --------------------------------------------------------------------------- #


class TestTables:
    def test_turkish_sttm_headers(self):
        roles = _match_roles(["Kaynak Tablo", "Kaynak Kolon", "Hedef Tablo", "Hedef Kolon",
                              "Dönüşüm Kuralı", "Job", "Yükleme Tipi"])
        assert set(roles) >= {"source_table", "source_column", "target_table", "target_column",
                              "transform", "job", "load"}

    def test_english_headers(self):
        roles = _match_roles(["Source Table", "Target Table", "Transformation", "Load Type"])
        assert set(roles) >= {"source_table", "target_table", "transform", "load"}

    def test_source_system_not_mistaken_for_source_table(self):
        roles = _match_roles(["Kaynak Sistem", "Kaynak Tablo", "Hedef Tablo"])
        assert roles["source_system"] == 0 and roles["source_table"] == 1

    def test_mapping_rows_become_flows_and_columns(self):
        builder, report = ModelBuilder(), DocReport("x.xlsx", "Excel")
        view = TableView(headers=["Kaynak Tablo", "Kaynak Kolon", "Hedef Tablo", "Hedef Kolon", "Job"],
                         rows=[["STG_A", "ID", "ODS_A", "ID", "SP_ODS_A"]], locator="STTM")
        assert interpret(view, builder, report, file="x.xlsx", reader="excel_sttm") == 1
        model = builder.build()
        assert any(e.kind.value == "column_maps_to" for e in model.edges)
        assert any(e.sources and e.sources[0].locator == "STTM · satır 1" for e in model.edges)

    def test_multiple_sources_in_one_cell(self):
        builder, report = ModelBuilder(), DocReport("x", "Excel")
        view = TableView(headers=["Kaynak Tablo", "Hedef Tablo"],
                         rows=[["STG_A, STG_B", "F_X"]], locator="S")
        assert interpret(view, builder, report, file="x", reader="t") == 2

    def test_job_dependency_table(self):
        builder, report = ModelBuilder(), DocReport("x", "Excel")
        view = TableView(headers=["Job", "Önceki İş"], rows=[["JOB_B", "JOB_A"]], locator="deps")
        interpret(view, builder, report, file="x", reader="t")
        model = builder.build()
        assert [e.kind.value for e in model.edges] == ["precedes"]

    def test_unrecognised_table_reported_not_swallowed(self):
        builder, report = ModelBuilder(), DocReport("x", "Word")
        view = TableView(headers=["Tablo", "Sahip", "Saklama"], rows=[["F_A", "DWH", "10 yıl"]],
                         locator="tablo 1")
        assert interpret(view, builder, report, file="x", reader="t") == 0
        assert any("tanınmadı" in note and "Sahip" in note for note in report.notes)


# --------------------------------------------------------------------------- #
# Metin kuralları
# --------------------------------------------------------------------------- #


class TestTextRules:
    @pytest.mark.parametrize("sentence,expected", [
        ("Veriler STG_A tablosundan ODS_A tablosuna yüklenir.", [("STG_A", "ODS_A")]),
        ("STG_A'dan ODS_A'ya aktarılır.", [("STG_A", "ODS_A")]),
        ("F_X tablosu ODS_A tablosundan beslenir.", [("ODS_A", "F_X")]),
        ("D_M ve F_X birleştirilerek MART_Y oluşturulur.", [("D_M", "MART_Y"), ("F_X", "MART_Y")]),
        ("STG_C, ODS_C tablosunu besler.", [("STG_C", "ODS_C")]),
        ("Akış: SRC_A -> A.dat -> STG_A", [("SRC_A", "A.dat"), ("A.dat", "STG_A")]),
        ("Data is loaded from STG_O into DWH.F_O every night.", [("STG_O", "DWH.F_O")]),
        ("DWH.D_P is populated from STG_P.", [("STG_P", "DWH.D_P")]),
    ])
    def test_patterns(self, sentence, expected):
        assert extract_sentence(sentence)[0] == expected

    def test_job_detected_from_suffix(self):
        pairs, job = extract_sentence("LOAD_F işi ODS_A tablosundan okuyup F_X tablosuna yazar.")
        assert pairs == [("ODS_A", "F_X")] and job == "LOAD_F"

    def test_akis_word_is_not_a_job(self):
        """Regresyon: "Akış:" kelimesindeki "ış" eki job işareti sanılıyordu."""
        assert extract_sentence("Akış: SRC_A -> STG_A")[1] is None

    def test_procedure_instrumental_suffix_is_a_job(self):
        """Regresyon: "prosedürüyle" tanınmadığı için job tablo sanılıyordu."""
        pairs, job = extract_sentence("STG_K'den ODS_K'ye SP_ODS_K prosedürüyle merge yapılır.")
        assert job == "SP_ODS_K" and pairs == [("STG_K", "ODS_K")]

    def test_merge_is_not_join(self):
        """DWH'de MERGE bir yükleme yöntemidir; job'ı kaynak tablo yapmamalı."""
        pairs, _ = extract_sentence("STG_K'den ODS_K'ye SP_ODS_K prosedürüyle merge yapılır.")
        assert ("SP_ODS_K", "ODS_K") not in pairs

    def test_acronyms_are_not_tables(self):
        """Regresyon: PDF başlığındaki "BDDK" tablo sanılıp uydurma akış üretiyordu."""
        assert not looks_like_table("BDDK")
        assert not looks_like_table("MUSTERI")
        assert looks_like_table("STG_MUSTERI")
        assert looks_like_table("DWH.F_SALES")
        assert looks_like_table("ACCT_20260101.dat")

    def test_plain_prose_yields_nothing(self):
        assert extract_sentence("Bu bölümde ETL standartları anlatılmaktadır.")[0] == []

    def test_markdown_tables_and_paragraphs(self):
        tables, paragraphs = split_markdown(
            "# Başlık\n\nSTG_A tablosundan ODS_A tablosuna yüklenir.\n\n"
            "| Kaynak Tablo | Hedef Tablo |\n|---|---|\n| ODS_A | F_A |\n")
        assert len(tables) == 1 and tables[0].rows == [["ODS_A", "F_A"]]
        assert paragraphs and "Başlık" in paragraphs[0][0]


# --------------------------------------------------------------------------- #
# Dosya türleri
# --------------------------------------------------------------------------- #


def _docx(path: Path, body_xml: str) -> Path:
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml",
                         f'<?xml version="1.0"?><w:document xmlns:w="{ns}"><w:body>{body_xml}</w:body></w:document>')
    return path


def _p(text: str) -> str:
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


class TestReaders:
    def test_excel_sheet(self, tmp_path):
        path = tmp_path / "sttm.xlsx"
        book = openpyxl.Workbook()
        book.active.append(["başlık satırı"])
        book.active.append(["Source Table", "Target Table", "Job"])
        book.active.append(["STG_A", "ODS_A", "SP_A"])
        book.save(path)
        result = ingest_files([path])
        assert {"STG_A", "ODS_A"} <= names(result.model)
        assert result.reports[0].status == "ok"

    def test_csv_semicolon(self, tmp_path):
        path = tmp_path / "m.csv"
        path.write_text("Kaynak Tablo;Hedef Tablo\nSTG_A;ODS_A\n", encoding="utf-8")
        assert {"STG_A", "ODS_A"} <= names(ingest_files([path]).model)

    def test_docx_paragraph_and_table(self, tmp_path):
        path = _docx(tmp_path / "d.docx",
                     _p("STG_A tablosundan ODS_A tablosuna yüklenir.")
                     + "<w:tbl><w:tr><w:tc>" + _p("Kaynak Tablo") + "</w:tc><w:tc>" + _p("Hedef Tablo")
                     + "</w:tc></w:tr><w:tr><w:tc>" + _p("ODS_A") + "</w:tc><w:tc>" + _p("F_A")
                     + "</w:tc></w:tr></w:tbl>")
        model = ingest_files([path]).model
        assert {"STG_A", "ODS_A", "F_A"} <= names(model)
        assert by_name(model, "F_A").status is Status.CONFIRMED  # tablodan geldi
        assert by_name(model, "STG_A").status is Status.DRAFT  # yalnız metinden

    def test_corrupt_docx_reported(self, tmp_path):
        path = tmp_path / "bozuk.docx"
        path.write_bytes(b"docx degil")
        report = ingest_files([path]).reports[0]
        assert report.status == "error"

    def test_sql_statements_are_separate_processes(self, tmp_path):
        """Tek süreç olsaydı 1. ifadenin kaynağı 2. ifadenin hedefine yanlışlıkla bağlanırdı."""
        path = tmp_path / "load.sql"
        path.write_text("INSERT INTO T1 SELECT * FROM S1;\nINSERT INTO T2 SELECT * FROM S2;", encoding="utf-8")
        from dwhflow.render.slices import dataset_links

        model = ingest_files([path]).model
        pairs = {(model.datasets[s].name, model.datasets[t].name) for s, t in dataset_links(model)}
        assert pairs == {("S1", "T1"), ("S2", "T2")}

    def test_sql_ctas_merge_cte(self, tmp_path):
        path = tmp_path / "x.sql"
        path.write_text(
            "CREATE TABLE MART_X AS SELECT a FROM F_A JOIN D_B ON 1=1;\n"
            "MERGE INTO ODS_C USING STG_C s ON 1=1 WHEN MATCHED THEN UPDATE SET x=s.x;\n"
            "INSERT INTO F_Y WITH c AS (SELECT * FROM STG_Y) SELECT * FROM c;",
            encoding="utf-8")
        found = names(ingest_files([path]).model)
        assert {"MART_X", "F_A", "D_B", "ODS_C", "STG_C", "F_Y", "STG_Y"} <= found
        assert "C" not in found  # CTE adı tablo değil

    def test_bteq_dot_commands_ignored(self, tmp_path):
        path = tmp_path / "x.bteq"
        path.write_text(".LOGON tdp/user,pw;\nINSERT INTO T1 SELECT * FROM S1;\n.LOGOFF;", encoding="utf-8")
        assert {"S1", "T1"} <= names(ingest_files([path]).model)

    def test_jil_chain(self, tmp_path):
        path = tmp_path / "x.jil"
        path.write_text("insert_job: A   job_type: CMD\nstart_times: \"01:00\"\n\n"
                        "insert_job: B   job_type: CMD\ncondition: s(A)\n", encoding="utf-8")
        model = ingest_files([path]).model
        assert [e.kind.value for e in model.edges] == ["precedes"]
        a = next(p for p in model.processes.values() if p.name == "A")
        assert a.schedule.start_times == ["01:00"]

    def test_pdf_text(self, tmp_path):
        path = tmp_path / "x.pdf"
        sample_script().write_pdf(path, ["STG_A tablosundan ODS_A tablosuna yuklenir."])
        assert {"STG_A", "ODS_A"} <= names(ingest_files([path]).model)

    def test_yaml_model(self):
        demo = SAMPLES.parent / "demo_bank_dwh" / "datasets.yaml"
        report = ingest_files([demo]).reports[0]
        assert report.reader == "DWHFlow modeli" and report.datasets > 10

    @pytest.mark.parametrize("name,hint", [("a.doc", ".docx"), ("a.xls", ".xlsx"), ("a.vsdx", "Visio")])
    def test_unsupported_types_explained(self, tmp_path, name, hint):
        path = tmp_path / name
        path.write_bytes(b"x")
        report = ingest_files([path]).reports[0]
        assert report.status == "unsupported" and hint in report.notes[0]

    def test_one_broken_file_does_not_stop_others(self, tmp_path):
        bad = tmp_path / "bozuk.xlsx"
        bad.write_bytes(b"excel degil")
        good = tmp_path / "ok.csv"
        good.write_text("Kaynak Tablo,Hedef Tablo\nSTG_A,ODS_A\n", encoding="utf-8")
        result = ingest_files([bad, good])
        assert result.reports[0].status == "error"
        assert {"STG_A", "ODS_A"} <= names(result.model)


# --------------------------------------------------------------------------- #
# Örnek dokümanlar: altı biçime dağılmış tek akış
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def result():
    return ingest_files(sorted(SAMPLES.iterdir()), name="Kart")


class TestSampleDocuments:
    def test_every_document_read(self, result):
        assert {r.status for r in result.reports} == {"ok"}
        assert len(result.reports) == 6

    def test_expected_tables_with_correct_layers(self, result):
        model = result.model
        expected = {
            "SRC_KART_ISLEM": Layer.SOURCE, "KART_ISLEM_YYYYMMDD.dat": Layer.INTERFACE,
            "STG_KART_ISLEM": Layer.STAGING, "ODS_KART_ISLEM": Layer.ODS,
            "DWH.D_MUSTERI": Layer.MART, "DWH.F_KART_ISLEM": Layer.MART,
            "DWH.MART_KART_OZET": Layer.MART, "EXP_BDDK_KART": Layer.CONSUMPTION,
            "BDDK_PORTAL": Layer.CONSUMPTION,
        }
        for name, layer in expected.items():
            assert by_name(model, name).layer is layer, name

    def test_no_invented_tables(self, result):
        """Regresyon: BDDK kısaltması ve SP_ODS_KART job'ı tablo sanılmıştı."""
        found = names(result.model)
        assert "BDDK" not in found
        assert "SP_ODS_KART" not in found
        assert len(found) == 12

    def test_schema_qualified_and_bare_names_merged(self, result):
        assert "F_KART_ISLEM" not in names(result.model)

    def test_downstream_system_hint(self, result):
        assert by_name(result.model, "BDDK_PORTAL").kind is DatasetKind.DOWNSTREAM_SYSTEM

    def test_unresolved_paragraph_offered_to_llm(self, result):
        assert result.llm_candidates == 1

    def test_unrecognised_tables_reported(self, result):
        notes = [n for r in result.reports for n in r.notes]
        assert any("Sahip" in n for n in notes)  # Word'deki sözlük tablosu
        assert any("Offset" in n for n in notes)  # Excel'deki alan yerleşimi

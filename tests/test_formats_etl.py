"""ETL ve zamanlayıcı biçimleri: IBM DataStage, Control-M, Apache Airflow.

Örnek dosyaların tam çıktısı ``test_samples_golden.py``'de sınanır; burada okuyucuların
kenar durumları, içerik tespiti, hata/boş raporları, güvenlik ve önizleme konumları sınanır.
"""

from __future__ import annotations

import re
import time
import zipfile
from pathlib import Path

import pytest

from dwhflow.ingest import ACCEPTED, PLUGIN_ERRORS, UNSUPPORTED, catalog, ingest_files, preview_document
from dwhflow.ingest.config import DEFAULT
from dwhflow.ingest.formats import _common, airflow, datastage
from dwhflow.ingest.formats._common import analyze_sql, mask_secrets
from dwhflow.ingest.formats.datastage import _resolve_files, _resolve_table, expand_isx
from dwhflow.ingest.registry import resolve
from dwhflow.model import EdgeKind, ProcessKind

FOLDER = Path(__file__).resolve().parents[1] / "src" / "dwhflow" / "examples" / "ornek_bicimler"
EXAMPLES = FOLDER.parent
OUR_KEYS = {"datastage", "datastage_xml", "controlm", "controlm_json", "airflow"}
SAMPLES = {
    "kart_datastage.dsx": "datastage",
    "kart_datastage_export.xml": "datastage_xml",
    "kart_controlm.xml": "controlm",
    "kart_controlm.json": "controlm_json",
    "kart_mart_dag.py": "airflow",
}


# --------------------------------------------------------------------------- #
# Yardımcılar
# --------------------------------------------------------------------------- #


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def read(path: Path):
    """(model, tek doküman raporu)"""
    result = ingest_files([path])
    reports = [r for r in result.reports if r.format != "arsiv"]
    assert len(reports) == 1, [r.as_dict() for r in result.reports]
    return result.model, reports[0]


def process_names(model) -> set[str]:
    return {p.name for p in model.processes.values()}


def dataset_names(model) -> set[str]:
    return {d.name for d in model.datasets.values()}


def precedes(model) -> set[tuple[str, str]]:
    names = {node_id: node.name for node_id, node in model.nodes.items()}
    return {(names[e.source], names[e.target]) for e in model.edges if e.kind is EdgeKind.PRECEDES}


def reads_writes(model, process_name: str) -> tuple[set[str], set[str]]:
    names = {node_id: node.name for node_id, node in model.nodes.items()}
    process = next(p for p in model.processes.values() if p.name == process_name)
    reads = {names[e.source] for e in model.edges if e.kind is EdgeKind.READS and e.target == process.id}
    writes = {names[e.target] for e in model.edges if e.kind is EdgeKind.WRITES and e.source == process.id}
    return reads, writes


def notes(report) -> str:
    return " | ".join(report.notes)


def _dsx_value(key: str, value: str, indent: str) -> list[str]:
    if "\n" in value or value.lstrip().startswith("<"):
        return [f"{indent}{key} =+=+=+=", value, "=+=+=+="]
    return [f'{indent}{key} "' + value.replace("\\", "\\\\").replace('"', '\\"') + '"']


def dsx_record(ident: str, ole: str, props: dict[str, str] | None = None,
               collections: dict[str, list[dict[str, str]]] | None = None) -> list[str]:
    lines = ["   BEGIN DSRECORD", f'      Identifier "{ident}"', f'      OLEType "{ole}"', '      Readonly "0"']
    for key, value in (props or {}).items():
        lines += _dsx_value(key, value, "      ")
    for name, items in (collections or {}).items():
        lines.append(f'      {name} "C{name}"')
        for item in items:
            lines.append("      BEGIN DSSUBRECORD")
            for key, value in item.items():
                lines += _dsx_value(key, value, "         ")
            lines.append("      END DSSUBRECORD")
    lines.append("   END DSRECORD")
    return lines


def dsx(*jobs: tuple[str, list[str]]) -> str:
    lines = ["BEGIN HEADER", '   CharacterSet "CP1252"', '   ServerName "TESTSRV"', "END HEADER"]
    for name, records in jobs:
        lines += ["BEGIN DSJOB", f'   Identifier "{name}"', *records, "END DSJOB"]
    return "\n".join(lines) + "\n"


def connector(usage: str) -> str:
    return (f"<?xml version='1.0' encoding='UTF-16'?><Properties version='1.1'><Common><Context type='int'>1"
            f"</Context></Common><Usage>{usage}</Usage></Properties >")


def root(name: str, job_type: str = "3", params: dict[str, str] | None = None) -> list[str]:
    return dsx_record("ROOT", "CJobDefn", {"Name": name, "JobType": job_type, "Category": "\\Jobs\\Test"},
                      {"Parameters": [{"Name": k, "Default": v, "ParamType": "0"}
                                      for k, v in (params or {}).items()]})


BOMB_XML = """<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
 <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
 <!ENTITY lol5 "&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;">
 <!ENTITY lol6 "&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;">
 <!ENTITY lol7 "&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;">
 <!ENTITY lol8 "&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;">
 <!ENTITY lol9 "&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;">
]>
"""


# --------------------------------------------------------------------------- #
# Kayıt ve örnekler
# --------------------------------------------------------------------------- #


class TestRegistration:
    def test_plugins_loaded_without_errors(self):
        assert not [e for e in PLUGIN_ERRORS if e.split(":")[0] in ("airflow", "controlm", "datastage")]

    def test_catalog_entries_and_samples(self):
        entries = {item["key"]: item for item in catalog()}
        assert OUR_KEYS <= set(entries)
        for key in OUR_KEYS:
            assert entries[key]["sniffs"] and entries[key]["preview"]
            assert (EXAMPLES / entries[key]["sample"]).is_file(), entries[key]["sample"]
        assert entries["datastage"]["group"] == "ETL" and entries["controlm"]["group"] == "Zamanlayıcı"

    def test_dsx_now_supported_and_isx_accepted(self):
        assert ".dsx" not in UNSUPPORTED
        assert ".isx" in ACCEPTED and ".dsx" in ACCEPTED

    @pytest.mark.parametrize("name", sorted(SAMPLES))
    def test_line_endings_do_not_matter(self, tmp_path, name):
        """``* text=auto`` ile örnekler Windows'ta CRLF, diğer sistemlerde LF gelir; sonuç aynı olmalı."""
        raw = (FOLDER / name).read_bytes().replace(b"\r\n", b"\n")
        results = []
        for folder, data in (("lf", raw), ("crlf", raw.replace(b"\n", b"\r\n"))):
            (tmp_path / folder).mkdir()
            path = tmp_path / folder / name
            path.write_bytes(data)
            model, report = read(path)
            _, blocks = preview_document(path)
            results.append((report.status, report.notes, process_names(model), dataset_names(model),
                            precedes(model), [(b["locator"], b.get("line_start"), b["text"].count("\n"))
                                              for b in blocks]))
        assert results[0] == results[1]

    @pytest.mark.parametrize("name,key", sorted(SAMPLES.items()))
    def test_sample_read_cleanly(self, name, key):
        model, report = read(FOLDER / name)
        assert report.format == key and report.status == "ok"
        assert report.processes and report.flows
        assert "beklenmeyen hata" not in notes(report)
        extractor = {"datastage": "etl:datastage", "datastage_xml": "etl:datastage", "controlm": "scheduler:controlm",
                     "controlm_json": "scheduler:controlm", "airflow": "scheduler:airflow"}[key]
        sources = [s for p in model.processes.values() for s in p.sources]
        assert sources and all(s.extractor == extractor and s.file == name for s in sources)


# --------------------------------------------------------------------------- #
# İçerik tespiti
# --------------------------------------------------------------------------- #


class TestSniffing:
    @pytest.mark.parametrize("name,key", sorted(SAMPLES.items()))
    def test_samples_resolved(self, name, key):
        spec, how, _ = resolve(FOLDER / name, DEFAULT)
        assert spec.key == key
        assert how == ("uzanti" if name.endswith(".dsx") else "icerik")

    def test_generic_suffixes_detected_by_content(self, tmp_path):
        copies = {
            "is_tanimi.txt": ("kart_datastage.dsx", "datastage"),
            "export.XML": ("kart_datastage_export.xml", "datastage_xml"),
            "zamanlama.txt": ("kart_controlm.xml", "controlm"),
            "akis.txt": ("kart_controlm.json", "controlm_json"),
            "dag_kopyasi.txt": ("kart_mart_dag.py", "airflow"),
        }
        for target, (source, key) in copies.items():
            path = tmp_path / target
            path.write_bytes((FOLDER / source).read_bytes())
            assert resolve(path, DEFAULT)[0].key == key, target

    @pytest.mark.parametrize("name,text", [
        ("ayarlar.json", '{"name": "dwh", "type": "Folder", "jobs": [{"Type": "job"}]}'),
        ("liste.json", '[{"Type": "Job:Command"}]'),
        ("yardimci.py", "import os\nfrom airflow.plugins_manager import AirflowPlugin\n\nclass P(AirflowPlugin):\n"
                        "    name = 'p'\n"),
        ("betik.py", "import pandas as pd\n\nDAG = 'yonlu graf'\nprint(DAG)\n"),
        ("pom.xml", '<?xml version="1.0"?><project><artifactId>x</artifactId><JOBNAME>y</JOBNAME></project>'),
        ("rapor.xml", "<rapor><satir>DEFTABLE ve DSExport sözcükleri metin içinde</satir></rapor>"),
        ("not.txt", "Airflow'da şöyle yazılır:\nfrom airflow import DAG\nwith DAG('x') as dag: bu satır kod değil\n"),
        ("anlatim.txt", "DataStage exportu BEGIN HEADER ile başlar; BEGIN DSJOB bölümleri içerir."),
    ])
    def test_unrelated_files_not_claimed(self, tmp_path, name, text):
        spec, _, _ = resolve(write(tmp_path, name, text), DEFAULT)
        assert spec is None or spec.key not in OUR_KEYS, spec.key

    def test_sniffers_do_not_steal_each_other(self):
        heads = {key: (FOLDER / name).read_bytes()[:65536] for name, key in SAMPLES.items()}
        sniffers = {"datastage": datastage.looks_like_dsx, "datastage_xml": datastage.looks_like_dsexport,
                    "airflow": airflow.looks_like_airflow}
        from dwhflow.ingest.formats import controlm
        sniffers.update(controlm=controlm.looks_like_controlm_xml, controlm_json=controlm.looks_like_controlm_json)
        for key, head in heads.items():
            claimed = {other for other, sniff in sniffers.items() if sniff(head, "dosya.txt")}
            assert claimed == {key}, (key, claimed)


# --------------------------------------------------------------------------- #
# DataStage
# --------------------------------------------------------------------------- #


def data_job(name: str, source_usage: str, target_usage: str, params: dict[str, str] | None = None,
             source_type: str = "OracleConnectorPX", target_type: str = "TeradataConnectorPX") -> list[str]:
    return [
        *root(name, params=params),
        *dsx_record("V0S0", "CCustomStage", {"Name": "SRC", "OutputPins": "V0S0P1", "StageType": source_type},
                    {"Properties": [{"Name": "XMLProperties", "Value": connector(source_usage)}]}),
        *dsx_record("V0S0P1", "CCustomOutput", {"Name": "l1", "Partner": "V0S1|V0S1P1"}),
        *dsx_record("V0S1", "CTransformerStage", {"Name": "TRX", "InputPins": "V0S1P1", "OutputPins": "V0S1P2",
                                                  "StageType": "CTransformerStage"}),
        *dsx_record("V0S1P1", "CTrxInput", {"Name": "l1", "Partner": "V0S0|V0S0P1"}),
        *dsx_record("V0S1P2", "CTrxOutput", {"Name": "l2", "Partner": "V0S2|V0S2P1"}),
        *dsx_record("V0S2", "CCustomStage", {"Name": "TGT", "InputPins": "V0S2P1", "StageType": target_type},
                    {"Properties": [{"Name": "XMLProperties", "Value": connector(target_usage)}]}),
        *dsx_record("V0S2P1", "CCustomInput", {"Name": "l2", "Partner": "V0S1|V0S1P2"}),
    ]


class TestDataStage:
    def test_sql_extraction_with_parameters(self, tmp_path):
        select = ("<GenerateSQL type='bool'><![CDATA[0]]></GenerateSQL><SQL><SelectStatement><![CDATA["
                  "LOCKING ROW FOR ACCESS SEL a.X FROM #pDb#.KAYNAK_A a JOIN KAYNAK_B b ON a.K = b.K "
                  "WHERE a.T = '#pTarih#']]></SelectStatement></SQL>")
        target = ("<TableName><![CDATA[#pTablo#]]></TableName><GenerateSQL><![CDATA[1]]></GenerateSQL>"
                  "<SQL><InsertStatement><![CDATA[INSERT INTO ESKI_SQL VALUES (1)]]></InsertStatement></SQL>"
                  "<BeforeAfter><AfterSQL><![CDATA[INSERT INTO LOG_TABLO SELECT * FROM KAYNAK_C]]></AfterSQL>"
                  "</BeforeAfter>")
        path = write(tmp_path, "a.dsx", dsx(("PX_TEST", data_job(
            "PX_TEST", select, target, params={"pTablo": "STG_HEDEF", "pDb": "$PROJDEF", "pTarih": "20240101"},
            source_type="TeradataConnectorPX"))))
        model, report = read(path)
        assert report.status == "ok", notes(report)
        reads, writes = reads_writes(model, "PX_TEST")
        # GenerateSQL=1 olan hedefte eski InsertStatement değil TableName kullanılır; After SQL de soy verir.
        assert reads == {"KAYNAK_A", "KAYNAK_B", "KAYNAK_C"}
        assert writes == {"STG_HEDEF", "LOG_TABLO"}
        process = next(p for p in model.processes.values() if p.name == "PX_TEST")
        assert process.kind is ProcessKind.ETL_MAPPING and process.tool == "DataStage"
        assert "paralel job" in process.description and "\\Jobs\\Test" in process.description

    def test_unresolved_table_parameter_is_noted(self, tmp_path):
        path = write(tmp_path, "b.dsx", dsx(("PX_P", data_job(
            "PX_P", "<TableName><![CDATA[#pKaynak#]]></TableName>", "<TableName><![CDATA[DWH.HEDEF]]></TableName>"))))
        model, report = read(path)
        assert dataset_names(model) == {"DWH.HEDEF"}
        assert "job PX_P · stage SRC: tablo adı parametreye bağlı (#pKaynak#)" in report.notes

    def test_sequence_precedence(self, tmp_path):
        def activity(ident, name, job=None, inputs="", outputs="", ole="CJSJobActivity"):
            props = {"Name": name, "InputPins": inputs, "OutputPins": outputs}
            if job:
                props["Jobname"] = job
            return dsx_record(ident, ole, props)

        def trigger(ident, name, partner, **extra):
            return dsx_record(ident, "CJSActivityOutput", {"Name": name, "Partner": partner, **extra})

        records = [
            *root("SEQ_TEST", "2", params={"pIkinci": "PX_B"}),
            *activity("V0S0", "JA_A", "PX_A", outputs="V0S0P1"),
            *trigger("V0S0P1", "OK", "V0S2|V0S2P1", ExpressionText="Executed OK"),
            *activity("V0S1", "JA_B", "#pIkinci#", outputs="V0S1P1"),
            *trigger("V0S1P1", "OK", "V0S2|V0S2P2"),
            *activity("V0S2", "SQ_HEPSI", ole="CJSSequencer", inputs="V0S2P1|V0S2P2", outputs="V0S2P3"),
            *trigger("V0S2P3", "Devam", "V0S3|V0S3P1"),
            *activity("V0S3", "JA_C", "PX_C", inputs="V0S3P1", outputs="V0S3P2|V0S3P3"),
            *trigger("V0S3P2", "Hata", "V0S4|V0S4P1", ExpressionText="Failed"),
            *trigger("V0S3P3", "Ozel", "V0S5|V0S5P1", ExpressionText="JA_C.$JobStatus <> DSJS.RUNFAILED"),
            *activity("V0S4", "JA_TEMIZLE", "PX_TEMIZLE", inputs="V0S4P1"),
            *activity("V0S5", "JA_D", "PX_D", inputs="V0S5P1"),
            *activity("V0S6", "JA_X", "#pYok#"),
        ]
        model, report = read(write(tmp_path, "seq.dsx", dsx(("SEQ_TEST", records))))
        assert precedes(model) == {("PX_A", "PX_C"), ("PX_B", "PX_C"), ("PX_C", "PX_D")}
        assert {"SEQ_TEST", "PX_A", "PX_B", "PX_C", "PX_D", "PX_TEMIZLE"} == process_names(model)
        seq = next(p for p in model.processes.values() if p.name == "SEQ_TEST")
        assert seq.kind is ProcessKind.BATCH_JOB and "çalıştırdığı job'lar: PX_A, PX_B, PX_C" in seq.description
        text = notes(report)
        assert "hata dalı (Hata) sıra kenarı sayılmadı" in text
        assert "stage JA_X: çağrılan job adı çözülemedi (#pYok#)" in text

    def test_referenced_job_takes_kind_from_its_definition(self, tmp_path):
        seq = [*root("SEQ", "2"),
               *dsx_record("V0S0", "CJSJobActivity", {"Name": "JA", "Jobname": "PX_SONRA"})]
        job = data_job("PX_SONRA", "<TableName>A</TableName>", "<TableName>B</TableName>")
        model, _ = read(write(tmp_path, "c.dsx", dsx(("SEQ", seq), ("PX_SONRA", job))))
        process = next(p for p in model.processes.values() if p.name == "PX_SONRA")
        assert process.kind is ProcessKind.ETL_MAPPING and process.description.startswith("DataStage paralel")

    def test_file_names(self):
        assert _resolve_files("#pDir#/KART_ISLEM_YYYYMMDD.dat", {}) == (["KART_ISLEM_YYYYMMDD.dat"], [])
        assert _resolve_files(r"\(2)\(1)\(3)file\(2)#pDir#/KART.dat\(2)0", {}) == (["KART.dat"], [])
        assert _resolve_files("#pDosya#", {"pDosya": "/veri/giris/MUSTERI.csv"}) == (["MUSTERI.csv"], [])
        assert _resolve_files("C:\\veri\\giris\\X.txt", {}) == (["X.txt"], [])
        names, problems = _resolve_files("#pDir#/#pDosya#", {})
        assert names == [] and problems

    def test_table_names(self):
        assert _resolve_table("#pDb#.STG_A", {}) == ("STG_A", None)
        assert _resolve_table("DWH.#pT#", {"pT": "F_X"}) == ("DWH.F_X", None)
        assert _resolve_table('"DWH"."D_MUSTERI"', {}) == ("DWH.D_MUSTERI", None)
        assert _resolve_table("#pT#", {})[0] is None
        assert _resolve_table("SELECT * FROM X", {})[0] is None

    def test_malformed_dsx_is_error(self, tmp_path):
        model, report = read(write(tmp_path, "bozuk.dsx", "bu bir dsx değil\nrastgele metin\n"))
        assert report.status == "error" and "biçimi tanınmadı" in notes(report) and not model.processes

    def test_truncated_dsx_still_reads_what_it_can(self, tmp_path):
        text = dsx(("PX_T", data_job("PX_T", "<TableName>K</TableName>", "<TableName>H</TableName>")))
        model, report = read(write(tmp_path, "yarim.dsx", text[: text.rindex("END DSJOB")]))
        assert process_names(model) == {"PX_T"} and dataset_names(model) == {"K", "H"}
        assert "kapanmadan dosya bitti" in notes(report)

    def test_header_only_is_empty(self, tmp_path):
        _, report = read(write(tmp_path, "bos.dsx", "BEGIN HEADER\n   ServerName \"X\"\nEND HEADER\n"
                                                    "BEGIN DSTABLEDEFS\nEND DSTABLEDEFS\n"))
        assert report.status == "empty" and "DataStage job'ı bulunamadı" in notes(report)
        assert "DSTABLEDEFS ×1" in notes(report)

    def test_dsexport_without_jobs_is_empty(self, tmp_path):
        _, report = read(write(tmp_path, "bos.xml", '<?xml version="1.0"?><DSExport><Header ServerName="X"/>'
                                                    "</DSExport>"))
        assert report.format == "datastage_xml" and report.status == "empty"

    def test_malformed_dsexport_is_error(self, tmp_path):
        _, report = read(write(tmp_path, "bozuk.xml", '<?xml version="1.0"?><DSExport><Job Identifier="A">'))
        assert report.format == "datastage_xml" and report.status == "error"
        assert "XML ayrıştırılamadı" in notes(report)

    def test_entity_bomb_in_connector_properties_is_rejected(self, tmp_path):
        bomb = BOMB_XML + "<Properties><Usage><TableName>&lol9;</TableName></Usage></Properties>"
        path = write(tmp_path, "bomba.dsx", dsx(("PX_B", data_job("PX_B", "<TableName>K</TableName>", "x")))
                     .replace(connector("x"), bomb))
        started = time.monotonic()
        model, report = read(path)
        assert time.monotonic() - started < 5
        assert "XMLProperties) okunamadı" in notes(report) and "güvenli olmayan XML" in notes(report)
        assert dataset_names(model) == {"K"}

    def test_preview_blocks_per_job(self):
        key, blocks = preview_document(FOLDER / "kart_datastage.dsx")
        assert key == "datastage"
        assert [b["locator"] for b in blocks] == ["job PX_STG_KART", "job PX_ODS_KART", "job PX_STG_MUSTERI",
                                                  "job SEQ_KART_GECE"]
        first = blocks[0]
        lines = (FOLDER / "kart_datastage.dsx").read_text(encoding="utf-8").splitlines()
        assert lines[first["line_start"] - 1] == "BEGIN DSJOB" and first["text"].endswith("END DSJOB")

    def test_xml_preview_uses_raw_text(self):
        _, blocks = preview_document(FOLDER / "kart_datastage_export.xml")
        text = (FOLDER / "kart_datastage_export.xml").read_text(encoding="utf-8")
        for block in blocks:
            assert block["text"].startswith("<Job Identifier=") and block["text"] in text
            assert text.splitlines()[block["line_start"] - 1].startswith("<Job Identifier=")


class TestIsx:
    def test_extracts_datastage_entries_safely(self, tmp_path):
        archive = tmp_path / "proje.isx"
        job = dsx(("PX_ISX", data_job("PX_ISX", "<TableName>ISX_KAYNAK</TableName>",
                                      "<TableName>ISX_HEDEF</TableName>")))
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("META-INF/IS_MANIFEST.MF", "Manifest-Version: 1.0\n")
            zf.writestr("DSSERVER/DWH_PROJ/Jobs/PX_ISX.pjb", job)
            zf.writestr("../../kacak.dsx", job.replace("PX_ISX", "PX_KACAK"))
            zf.writestr("Jobs/export.xml", (FOLDER / "kart_datastage_export.xml").read_text(encoding="utf-8"))
            zf.writestr("Jobs/not.sql", "SELECT 1")
        workdir = tmp_path / "acik"
        workdir.mkdir()
        extracted = expand_isx(archive, workdir)
        assert len(extracted) == 3 and all(p.parent == workdir for p in extracted)
        assert not (tmp_path / "kacak.dsx").exists()

        result = ingest_files([archive])
        by_file = {r.file: r for r in result.reports}
        assert by_file["proje.isx"].format == "arsiv" and by_file["proje.isx"].status == "ok"
        assert by_file["PX_ISX.pjb"].format == "datastage" and by_file["PX_ISX.pjb"].container == "proje.isx"
        assert by_file["export.xml"].format == "datastage_xml"
        assert {"PX_ISX", "PX_KACAK", "PX_LOAD_F_KART_ISLEM"} <= process_names(result.model)

    def test_archive_without_datastage_is_error(self, tmp_path):
        archive = tmp_path / "bos.isx"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("META-INF/IS_MANIFEST.MF", "Manifest-Version: 1.0\n")
        report = ingest_files([archive]).reports[0]
        assert report.status == "error" and "DataStage export" in notes(report)

    def test_not_a_zip_is_error(self, tmp_path):
        report = ingest_files([write(tmp_path, "sahte.isx", "zip değil")]).reports[0]
        assert report.status == "error"


# --------------------------------------------------------------------------- #
# Control-M
# --------------------------------------------------------------------------- #

CONTROLM_XML = """<?xml version="1.0" encoding="utf-8"?>
<DEFTABLE xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <FOLDER DATACENTER="DC" FOLDER_NAME="F1">
    <JOB JOBNAME="A" TIMEFROM="2330" WEEKDAYS="0,6" CMDLINE="sqlplus etl/Sifre123@DWH @/etl/sql/a.sql">
      <OUTCOND NAME="A-OK" ODATE="ODAT" SIGN="+"/>
    </JOB>
    <JOB JOBNAME="B" DAYS="1,15" WEEKDAYS="L1">
      <OUTCOND NAME="B-OK" ODATE="ODAT" SIGN="ADD"/>
    </JOB>
    <JOB JOBNAME="C">
      <INCOND NAME="A-OK" ODATE="ODAT" AND_OR="A"/>
      <INCOND NAME="B-OK" ODATE="ODAT" AND_OR="O"/>
      <OUTCOND NAME="A-OK" ODATE="ODAT" SIGN="-"/>
    </JOB>
    <JOB JOBNAME="D">
      <INCOND NAME="DIS-KOSUL" ODATE="ODAT" AND_OR="A"/>
      <INCOND NAME="C-HATA" ODATE="ODAT" AND_OR="A"/>
    </JOB>
    <JOB JOBNAME="E" TASKTYPE="Dummy">
      <INCOND NAME="A-OK" ODATE="PREV" AND_OR="A"/>
    </JOB>
    <JOB JOBNAME="C2">
      <ON STMT="*" CODE="NOTOK"><DOCOND NAME="C-HATA" ODATE="ODAT" SIGN="+"/></ON>
    </JOB>
    <JOB JOBNAME="G"><INCOND NAME="SILINEN" ODATE="ODAT" AND_OR="A"/></JOB>
    <JOB JOBNAME="H"><OUTCOND NAME="SILINEN" ODATE="ODAT" SIGN="-"/></JOB>
    <JOB DESCRIPTION="adsiz"/>
  </FOLDER>
</DEFTABLE>
"""


class TestControlM:
    def test_conditions(self, tmp_path):
        model, report = read(write(tmp_path, "ctm.xml", CONTROLM_XML))
        assert report.format == "controlm" and report.status == "ok"
        assert precedes(model) == {("A", "C"), ("B", "C")}
        text = notes(report)
        assert "C: koşullar VEYA (OR) ile bağlı (A-OK | B-OK)" in text
        assert "D: dış koşul (bu dosyada üreten job yok): DIS-KOSUL" in text
        assert "D: koşul yalnız ON/DOCOND ile" in text and "C-HATA" in text
        assert "E: başka güne bağlı koşul sıra sayılmadı: A-OK (PREV)" in text
        assert "G: dış koşul (bu dosyada üreten job yok): SILINEN" in text
        assert "1 job'ın adı (JOBNAME) yok" in text

    def test_schedule_and_description(self, tmp_path):
        model, _ = read(write(tmp_path, "ctm.xml", CONTROLM_XML))
        jobs = {p.name: p for p in model.processes.values()}
        assert jobs["A"].schedule.start_times == ["23:30"] and jobs["A"].schedule.days_of_week == ["sat", "sun"]
        assert jobs["A"].kind is ProcessKind.BATCH_JOB and jobs["A"].tool == "Control-M"
        assert "etl/***@DWH" in jobs["A"].description and "Sifre123" not in jobs["A"].description
        assert "SQL betiği /etl/sql/a.sql" in jobs["A"].description
        assert "ayın günleri: 1,15" in jobs["B"].description and "hafta günleri: L1" in jobs["B"].description
        assert jobs["B"].schedule is None
        assert "Dummy (boş) job" in jobs["E"].description
        assert jobs["A"].sources[0].locator == "klasör F1 · job A"

    def test_json_flow_events_and_subfolders(self, tmp_path):
        text = """{
          "Defaults": {"Host": "varsayilan-host", "Application": "DWH"},
          "KLASOR": {
            "Type": "SimpleFolder",
            "J1": {"Type": "Job:Command", "Command": "dsjob -run -jobstatus PROJE PX_J1",
                   "When": {"FromTime": "0715", "WeekDays": ["MON", "WED"], "MonthDays": ["ALL"]},
                   "eventsToAdd": {"Type": "AddEvents", "Events": [{"Event": "J1-OK"}]}},
            "J2": {"Type": "Job:Script", "FileName": "j2.sh", "FilePath": "/etl/bin",
                   "Bekle": {"Type": "WaitForEvents", "Events": [{"Event": "J1-OK"}, "OR", {"Event": "ELLE"}]}},
            "ALT": {"Type": "SubFolder",
                    "J3": {"Type": "Job:Dummy"},
                    "J4": {"Type": "Job:Database:EmbeddedQuery", "Query": "INSERT INTO X SELECT * FROM Y"}},
            "Sira": {"Type": "Flow", "Sequence": ["J2", "J3", "YOK", "J4"]}
          }
        }"""
        model, report = read(write(tmp_path, "ctm.json", text))
        assert report.format == "controlm_json" and report.status == "ok"
        assert precedes(model) == {("J1", "J2"), ("J2", "J3"), ("J3", "J4")}
        jobs = {p.name: p for p in model.processes.values()}
        assert jobs["J1"].schedule.start_times == ["07:15"] and jobs["J1"].schedule.days_of_week == ["mon", "wed"]
        assert "DataStage job'ı PX_J1 (proje PROJE)" in jobs["J1"].description
        assert "sunucu: varsayilan-host" in jobs["J1"].description
        assert "betik: /etl/bin/j2.sh" in jobs["J2"].description
        assert "gömülü SQL sorgusu" in jobs["J4"].description
        assert jobs["J3"].sources[0].locator == "klasör KLASOR/ALT · job J3"
        assert not model.datasets  # Control-M açıklamadaki SQL'den soy çıkarmaz
        text = notes(report)
        assert "klasör KLASOR · akış Sira: tanımsız job 'YOK' atlandı" in text
        assert "J2: koşullar VEYA (OR) ile bağlı (J1-OK | ELLE)" in text

    @pytest.mark.parametrize("name,text,message", [
        ("yarim.xml", '<?xml version="1.0"?><DEFTABLE><FOLDER FOLDER_NAME="F"><JOB JOBNAME="A">',
         "XML ayrıştırılamadı"),
        ("yarim.json", '{"F": {"Type": "Folder", "A": {"Type": "Job:Command", ', "JSON ayrıştırılamadı"),
        ("bomba.xml", BOMB_XML + '<DEFTABLE><JOB JOBNAME="&lol9;"/></DEFTABLE>', "güvenli olmayan XML"),
    ])
    def test_malformed_is_error(self, tmp_path, name, text, message):
        started = time.monotonic()
        model, report = read(write(tmp_path, name, text))
        assert time.monotonic() - started < 5
        assert report.format in ("controlm", "controlm_json") and report.status == "error"
        assert message in notes(report) and not model.processes

    @pytest.mark.parametrize("name,text", [
        ("bos.xml", '<?xml version="1.0"?><DEFTABLE><SMART_FOLDER FOLDER_NAME="F"/></DEFTABLE>'),
        ("bos.json", '{"F": {"Type": "Folder", "Description": "boş"}}'),
    ])
    def test_no_jobs_is_empty(self, tmp_path, name, text):
        _, report = read(write(tmp_path, name, text))
        assert report.status == "empty" and "Control-M job'ı bulunamadı" in notes(report)

    def test_xml_preview_has_raw_job_text(self):
        _, blocks = preview_document(FOLDER / "kart_controlm.xml")
        lines = (FOLDER / "kart_controlm.xml").read_text(encoding="utf-8").splitlines()
        assert len(blocks) == 8
        for block in blocks:
            assert block["text"].lstrip().startswith("<JOB ") and block["text"].endswith("</JOB>")
            assert lines[block["line_start"] - 1].lstrip().startswith("<JOB ")


# --------------------------------------------------------------------------- #
# Airflow
# --------------------------------------------------------------------------- #


class TestAirflow:
    def test_dependency_forms(self, tmp_path):
        source = """
from airflow import DAG
from airflow.models.baseoperator import chain, chain_linear, cross_downstream
from airflow.operators.empty import EmptyOperator as E

with DAG("bagimlilik", schedule="@daily") as dag:
    a, b, c, d = E(task_id="a"), E(task_id="b"), E(task_id="c"), E(task_id="d")
    e = E(task_id="e"); f = E(task_id="f"); g = E(task_id="g"); h = E(task_id="h")
    a >> [b, c] >> d
    chain(d, [e, f], [g, h])
    i, j, k, m = [E(task_id=x) for x in ("i", "j", "k", "m")]
    n = E(task_id="n"); o = E(task_id="o"); p = E(task_id="p"); q = E(task_id="q")
    chain_linear(n, [o, p], q)
    r = E(task_id="r"); s = E(task_id="s"); t = E(task_id="t"); u = E(task_id="u")
    cross_downstream([r, s], [t, u])
    v = E(task_id="v"); w = E(task_id="w"); x = E(task_id="x")
    v.set_downstream(w)
    x.set_upstream(v)
    y = E(task_id="y"); z = E(task_id="z")
    z << y
    [y, z] >> [v, w]
"""
        model, report = read(write(tmp_path, "bagimlilik.py", source))
        assert precedes(model) == {
            ("a", "b"), ("a", "c"), ("b", "d"), ("c", "d"), ("d", "e"), ("d", "f"), ("e", "g"), ("f", "h"),
            ("n", "o"), ("n", "p"), ("o", "q"), ("p", "q"),
            ("r", "t"), ("r", "u"), ("s", "t"), ("s", "u"), ("v", "w"), ("v", "x"), ("y", "z"),
        }
        text = notes(report)
        assert "liste >> liste Airflow'da geçersiz" in text
        process = next(p for p in model.processes.values() if p.name == "a")
        assert process.schedule.cron == "@daily" and process.schedule.start_times == ["00:00"]
        # ``EmptyOperator as E`` takma adı gerçek sınıf adına çözülür.
        assert process.kind is ProcessKind.BATCH_JOB
        assert process.description == "Airflow DAG: bagimlilik · EmptyOperator"

    def test_task_group_prefixes(self, tmp_path):
        source = """
from airflow import DAG
from airflow.operators.empty import EmptyOperator
from airflow.utils.task_group import TaskGroup

with DAG(dag_id="gruplar") as dag:
    start = EmptyOperator(task_id="start")
    with TaskGroup("dis") as dis:
        with TaskGroup(group_id="ic") as ic:
            x = EmptyOperator(task_id="x")
            y = EmptyOperator(task_id="y")
            x >> y
        z = EmptyOperator(task_id="z")
        ic >> z
    with TaskGroup("duz", prefix_group_id=False) as duz:
        w = EmptyOperator(task_id="w")
    start >> dis >> duz
"""
        model, _ = read(write(tmp_path, "gruplar.py", source))
        assert process_names(model) == {"start", "dis.ic.x", "dis.ic.y", "dis.z", "w"}
        assert precedes(model) == {("dis.ic.x", "dis.ic.y"), ("dis.ic.y", "dis.z"), ("start", "dis.ic.x"),
                                   ("dis.z", "w")}

    def test_taskflow(self, tmp_path):
        source = """
from airflow.decorators import dag, task, task_group

@dag(schedule="15 3 * * 1-5", dag_id="akis_tf")
def akis():
    @task
    def cek():
        return 1

    @task.bash
    def yaz(deger):
        return f"echo {deger}"

    @task(task_id="ozel_ad")
    def son(a, b):
        pass

    @task_group(group_id="grp")
    def grup(v):
        return yaz(v)

    v1 = cek()
    v2 = cek()
    sonuc = son(grup(v1), v2)
    cek.override(task_id="yeniden")() >> sonuc

akis()
"""
        model, report = read(write(tmp_path, "tf.py", source))
        assert report.status == "ok", notes(report)
        assert process_names(model) == {"cek", "cek__1", "grp.yaz", "ozel_ad", "yeniden"}
        assert precedes(model) == {("cek", "grp.yaz"), ("grp.yaz", "ozel_ad"), ("cek__1", "ozel_ad"),
                                   ("yeniden", "ozel_ad")}
        jobs = {p.name: p for p in model.processes.values()}
        assert jobs["grp.yaz"].kind is ProcessKind.SHELL_SCRIPT and "@task.bash" in jobs["grp.yaz"].description
        assert jobs["cek"].schedule.start_times == ["03:15"]
        assert jobs["cek"].schedule.days_of_week == ["mon", "tue", "wed", "thu", "fri"]
        assert jobs["cek"].description.startswith("Airflow DAG: akis_tf")

    def test_sql_lineage_jinja_and_non_literals(self, tmp_path):
        source = '''
from airflow import DAG
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator
from airflow.providers.google.cloud.operators.bigquery import BigQueryInsertJobOperator
from textwrap import dedent

SORGU = "INSERT INTO H_DEGISKEN SELECT * FROM K_DEGISKEN"
tablo = "K_FSTRING"

with DAG("sql_dag") as dag:
    t1 = SQLExecuteQueryOperator(
        task_id="t1",
        sql="INSERT INTO {{ params.db }}.HEDEF_A SELECT * FROM KAYNAK_A "
            "WHERE D = '{{ ds }}' AND X IN ({% for k in params.k %}{{ k }},{% endfor %} 0) {# yorum #}",
    )
    t2 = SQLExecuteQueryOperator(task_id="t2", sql=SORGU)
    t3 = SQLExecuteQueryOperator(task_id="t3", sql=f"INSERT INTO H_F SELECT * FROM {tablo}")
    t4 = BigQueryInsertJobOperator(
        task_id="t4",
        configuration={"query": {"query": "INSERT INTO ds.H_BQ SELECT * FROM ds.K_BQ", "useLegacySql": False}},
    )
    t5 = SQLExecuteQueryOperator(task_id="t5", sql="INSERT INTO H_{{ ds_nodash }} SELECT * FROM K_SABIT")
    t6 = SQLExecuteQueryOperator(task_id="t6", sql=["sql/yukle.sql", dedent("""
        SELECT * FROM OKUNAN_T""")])
    t1 >> t2 >> t3 >> t4 >> t5 >> t6
'''
        model, report = read(write(tmp_path, "sql_dag.py", source))
        assert dataset_names(model) == {"HEDEF_A", "KAYNAK_A", "ds.H_BQ", "ds.K_BQ", "K_SABIT", "OKUNAN_T"}
        assert reads_writes(model, "t1") == ({"KAYNAK_A"}, {"HEDEF_A"})
        assert reads_writes(model, "t5") == ({"K_SABIT"}, set())
        assert reads_writes(model, "t6") == ({"OKUNAN_T"}, set())
        assert reads_writes(model, "t2") == (set(), set()) and reads_writes(model, "t3") == (set(), set())
        jobs = {p.name: p for p in model.processes.values()}
        assert jobs["t1"].kind is ProcessKind.SQL_SCRIPT and jobs["t4"].kind is ProcessKind.SQL_SCRIPT
        text = notes(report)
        assert "görev t2: SQL sabit metin değil" in text and "görev t3: SQL sabit metin değil" in text
        assert "görev t5: 1 tablo adı şablona bağlı" in text
        assert "görev t6: SQL dosyası 'sql/yukle.sql' okunmadı" in text

    def test_two_dags_same_task_id_and_dag_kwarg(self, tmp_path):
        source = """
from airflow import DAG
from airflow.operators.bash import BashOperator

d1 = DAG("birinci", schedule_interval="15 1 * * *")
d2 = DAG(dag_id="ikinci", schedule=None)
a = BashOperator(task_id="yukle", bash_command="x.sh ", dag=d1)
with d2:
    b = BashOperator(task_id="yukle", bash_command="y.sh ")
    c = BashOperator(task_id="bitir", bash_command="z.sh ")
    b >> c
a >> c
"""
        model, report = read(write(tmp_path, "iki.py", source))
        assert process_names(model) == {"yukle", "ikinci.yukle", "bitir"}
        assert precedes(model) == {("ikinci.yukle", "bitir")}
        jobs = {p.name: p for p in model.processes.values()}
        assert jobs["yukle"].schedule.start_times == ["01:15"] and jobs["bitir"].schedule is None
        assert "zamanlama yok" in jobs["bitir"].description
        assert jobs["yukle"].kind is ProcessKind.SHELL_SCRIPT
        assert "farklı DAG'lardaki görevler" in notes(report)

    def test_dynamic_constructs_are_skipped_with_notes(self, tmp_path):
        source = """
from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator

with DAG("dinamik") as dag:
    s = EmptyOperator(task_id="s")
    m = BashOperator.partial(task_id="haritali", retries=1).expand(bash_command=["a", "b"])
    s >> m
    for i in range(3):
        EmptyOperator(task_id=f"dinamik_{i}") >> m
    s >> bilinmeyen_gorev
EmptyOperator(task_id="dagsiz")
"""
        model, report = read(write(tmp_path, "dinamik.py", source))
        assert process_names(model) == {"s", "haritali"}
        assert precedes(model) == {("s", "haritali")}
        text = notes(report)
        assert "döngü statik olarak açılamadı" in text
        assert "task_id sabit (boş olmayan) metin değil" in text
        assert "bağımlılığın bir ucu statik olarak çözülemedi" in text
        assert "'dagsiz' görevi bir DAG'a bağlı değil" in text

    def test_file_is_never_executed(self, tmp_path):
        marker = tmp_path / "calisti.txt"
        source = f"""
from pathlib import Path
from airflow import DAG
from airflow.operators.empty import EmptyOperator

Path({str(marker)!r}).write_text("dosya çalıştırıldı")
raise SystemExit("okuyucu bu dosyayı çalıştırmamalı")

with DAG("guvenli") as dag:
    EmptyOperator(task_id="tek")
"""
        model, report = read(write(tmp_path, "guvenli.py", source))
        assert not marker.exists()
        assert report.status == "ok" and process_names(model) == {"tek"}

    @pytest.mark.parametrize("source,status,message", [
        ("from airflow import DAG\nwith DAG('x') as dag\n    pass\n", "error", "sözdizimi hatası"),
        ("from airflow import DAG\n\n# DAG burada tanımlanmıyor\nX = 1\n", "empty", "Airflow DAG'ı bulunamadı"),
        ("from airflow import DAG\nwith DAG('bos') as dag:\n    pass\n", "empty", "görev bulunamadı"),
    ])
    def test_error_and_empty(self, tmp_path, source, status, message):
        _, report = read(write(tmp_path, "d.py", source))
        assert report.format == "airflow" and report.status == status and message in notes(report)

    def test_deep_chain_does_not_crash(self, tmp_path):
        tasks = "\n".join(f"    t{i} = E(task_id='t{i}')" for i in range(400))
        chain_expr = " >> ".join(f"t{i}" for i in range(400))
        source = f"from airflow import DAG\nfrom airflow.operators.empty import EmptyOperator as E\n" \
                 f"with DAG('uzun') as dag:\n{tasks}\n    {chain_expr}\n"
        model, report = read(write(tmp_path, "uzun.py", source))
        assert report.status == "ok" and len(precedes(model)) == 399

    def test_size_cap(self, tmp_path, monkeypatch):
        monkeypatch.setattr(airflow, "MAX_BYTES", 100)
        path = write(tmp_path, "buyuk.py", "from airflow import DAG\n" + "# dolgu\n" * 50)
        _, report = read(path)
        assert report.status == "error" and "dosya çok büyük" in notes(report)


def test_size_cap_xml(tmp_path, monkeypatch):
    monkeypatch.setattr(_common, "MAX_BYTES", 200)
    _, report = read(write(tmp_path, "buyuk.xml", CONTROLM_XML))
    assert report.status == "error" and "dosya çok büyük" in notes(report)


# --------------------------------------------------------------------------- #
# Kötü niyetli / bozuk girdiler: okuyucu hiçbir zaman istisna fırlatmaz
# --------------------------------------------------------------------------- #

HOSTILE = {
    "bos": b"",
    "ikili": bytes(range(256)) * 8,
    "yalniz_kucuktur": b"<",
    "yalniz_suslu": b"{",
    "utf16_xml": '<?xml version="1.0" encoding="UTF-16"?><DEFTABLE><JOB JOBNAME="Ş"/></DEFTABLE>'.encode("utf-16"),
    "bilinmeyen_kodlama": b'<?xml version="1.0" encoding="bogus-8"?><DEFTABLE/>',
    "dis_varlik": b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY e SYSTEM "file:///etc/passwd">]><DEFTABLE a="&e;"/>',
    "bomba": (BOMB_XML + "<DSExport><Job Identifier='&lol9;'/></DSExport>").encode(),
    "derin_xml": (b"<DSExport><Job Identifier='A'><Record Identifier='V0S0' Type='CustomStage'>"
                  + b"<Collection Name='Properties'><SubRecord>" * 3000
                  + b"</SubRecord></Collection>" * 3000 + b"</Record></Job></DSExport>"),
    "derin_ctm": b"<DEFTABLE>" + b"<SUB_FOLDER JOBNAME='A'>" * 3000 + b"<JOB JOBNAME='J'/>"
                 + b"</SUB_FOLDER>" * 3000 + b"</DEFTABLE>",
    "derin_dsx": b"BEGIN HEADER\nEND HEADER\nBEGIN DSJOB\n   Identifier \"D\"\n" + b"BEGIN DSRECORD\n" * 3000,
    "derin_json": b'{"a":' * 5000 + b"1" + b"}" * 5000,
    "derin_py": b"from airflow import DAG\nx = " + b"[" * 3000 + b"]" * 3000 + b"\n",
    "uzun_toplam_py": (b"from airflow import DAG\nwith DAG('x') as d:\n    y = "
                       + b" + ".join([b"'a'"] * 5000) + b"\n"),
    "bozuk_json_tipi": b'{"F": {"Type": "Folder", "J": {"Type": "Job:Command", "When": "x", "Events": 5,'
                       b' "eventsToWaitFor": {"Events": "yok"}, "Description": 7}}}',
    "null_py": b"from airflow import DAG\x00\nDAG('x')\n",
}


@pytest.mark.parametrize("reader", [datastage.read_datastage, airflow.read_airflow,
                                    __import__("dwhflow.ingest.formats.controlm", fromlist=["x"]).read_controlm],
                         ids=["datastage", "airflow", "controlm"])
@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_readers_never_raise(tmp_path, reader, name):
    from dwhflow.ingest.base import DocReport, ModelBuilder

    path = tmp_path / f"{name}.dat"
    path.write_bytes(HOSTILE[name])
    builder, report = ModelBuilder(), DocReport(file=path.name, reader="test")
    started = time.monotonic()
    reader(builder, report, path)
    builder.build()
    assert time.monotonic() - started < 10
    assert report.status in ("ok", "empty", "error")
    if report.status == "error":
        assert report.notes


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_previews_never_raise(tmp_path, name):
    from dwhflow.ingest.formats import controlm

    path = tmp_path / f"{name}.dat"
    path.write_bytes(HOSTILE[name])
    for previewer in (datastage.preview_datastage, controlm.preview_controlm):
        blocks = previewer(path)
        assert isinstance(blocks, list) and blocks


# --------------------------------------------------------------------------- #
# Ortak yardımcılar
# --------------------------------------------------------------------------- #


class TestCommon:
    def test_mask_secrets(self):
        masked = mask_secrets("dsjob -domain D:9443 -user dsadm -password Gizli123 -run P J")
        assert "Gizli123" not in masked and "-password ***" in masked
        assert mask_secrets("sqlplus etl/Sifre@DWH @x.sql") == "sqlplus etl/***@DWH @x.sql"
        assert mask_secrets("/data/in/dosya@2024.dat") == "/data/in/dosya@2024.dat"

    def test_analyze_sql(self):
        result = analyze_sql("-- açıklama\nLOCKING ROW FOR ACCESS SEL * FROM A; DELETE FROM B; "
                             "INSERT INTO C SELECT * FROM D JOIN DWHFLOW_COZULEMEYEN ON 1=1;"
                             "WITH x AS (SELECT * FROM E) SELECT * FROM x")
        assert [(s.target, s.sources) for s in result.statements] == [
            (None, ["A"]), ("C", ["D"]), (None, ["E"])]
        assert result.unresolved == 1 and result.unparsed == 0


# --------------------------------------------------------------------------- #
# Önizleme konumları okuyucu konumlarıyla eşleşir
# --------------------------------------------------------------------------- #


def _matches(locator: str, blocks: list[dict]) -> bool:
    for block in blocks:
        if locator == block["locator"] or locator.startswith(block["locator"] + " · "):
            return True
        line = re.fullmatch(r"satır (\d+)", locator)
        if line and block["kind"] == "code" and "line_start" in block:
            start = block["line_start"]
            if start <= int(line.group(1)) < start + block["text"].count("\n") + 1:
                return True
    return False


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_preview_locators_match_reader(name):
    path = FOLDER / name
    key, blocks = preview_document(path)
    assert key == SAMPLES[name] and blocks and all(b["kind"] == "code" for b in blocks)
    model = ingest_files([path]).model
    locators = {s.locator for node in model.nodes.values() for s in node.sources}
    locators |= {s.locator for edge in model.edges for s in edge.sources}
    assert locators and None not in locators
    unmatched = sorted(loc for loc in locators if not _matches(loc, blocks))
    assert not unmatched, unmatched

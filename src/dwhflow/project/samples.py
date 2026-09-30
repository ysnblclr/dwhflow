"""Örnekler ve şablonlar (PLAN_V2 US-0.4, US-0.5).

* Senaryolar: kredi kartı akışı, 6 biçimde 6 doküman (``examples/ornek_dokumanlar``) ve aynı akışın
  DataStage + Control-M + Airflow ile kurulmuş hâli (biçim örneklerinden seçilen 5 dosya).
* Biçim örnekleri: her desteklenen biçimden bir dosya ve beklenen sonucu (``examples/ornek_bicimler``).
* Boş şablonlar: bellekte üretilir (STTM ve job bağımlılık Excel'i, tasarım dokümanı Word'ü);
  şablonla yazılan doküman LLM'siz, kurallarla eksiksiz okunur.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from ..ingest.config import DEFAULT
from ..ingest.registry import resolve

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
FORMATS_DIR = EXAMPLES / "ornek_bicimler"
EXPECTED = ".beklenen.json"
#: Senaryo ya bir klasörün tüm dosyaları (``folder``) ya da seçilmiş dosyalardır (``files``).
SCENARIOS: dict[str, dict[str, Any]] = {
    "kart": {"title": "Kredi kartı işlemleri",
             "description": "Kaynak sistemden dosyaya, staging'den mart'a ve BDDK bildirimine uçtan uca akış. "
                            "Excel STTM, Word tasarım dokümanı, PDF, SQL, AutoSys JIL ve operasyon notu.",
             "folder": EXAMPLES / "ornek_dokumanlar"},
    "etl": {"title": "Kurum ETL zinciri: DataStage · Control-M · Airflow",
            "description": "Aynı kredi kartı akışı kurum araçlarıyla: DataStage job'ları (.dsx), Control-M gece "
                           "zinciri (XML), Airflow mart DAG'ı (.py), Excel STTM ve Word tasarım dokümanı.",
            "files": [FORMATS_DIR / "kart_datastage.dsx", FORMATS_DIR / "kart_controlm.xml",
                      FORMATS_DIR / "kart_mart_dag.py", EXAMPLES / "ornek_dokumanlar" / "STTM_Kart_Islemleri.xlsx",
                      EXAMPLES / "ornek_dokumanlar" / "Kart_Tasarim_Dokumani.docx"]},
}


def _scenario_paths(key: str) -> list[Path]:
    scenario = SCENARIOS[key]
    if "files" in scenario:
        return [path for path in scenario["files"] if path.is_file()]
    folder: Path = scenario["folder"]
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and not p.name.endswith(EXPECTED))


def _format_label(path: Path) -> tuple[str | None, str]:
    spec, _, _ = resolve(path, DEFAULT)
    return (spec.key, spec.label) if spec else (None, "desteklenmiyor")


def catalog() -> dict[str, Any]:
    scenarios = []
    for key, scenario in SCENARIOS.items():
        files = []
        for path in _scenario_paths(key):
            fmt, label = _format_label(path)
            files.append({"name": path.name, "format": fmt, "label": label, "size": path.stat().st_size})
        scenarios.append({"id": key, "title": scenario["title"], "description": scenario["description"],
                          "files": files})
    formats = []
    if FORMATS_DIR.is_dir():
        for path in sorted(FORMATS_DIR.iterdir()):
            if not path.is_file() or path.name.endswith(EXPECTED) or path.name == "README.md":
                continue
            expected_file = path.parent / (path.name + EXPECTED)
            expected = json.loads(expected_file.read_text(encoding="utf-8")) if expected_file.is_file() else {}
            fmt, label = _format_label(path)
            formats.append({
                "name": path.name, "format": fmt, "label": label, "size": path.stat().st_size,
                "description": expected.get("aciklama", ""),
                "expected": {k: len(expected.get(k, [])) for k in ("datasets", "processes", "links", "precedes")},
            })
    return {"scenarios": scenarios, "formats": formats,
            "templates": [{"id": key, **{k: v for k, v in value.items() if k != "build"}}
                          for key, value in TEMPLATES.items()]}


def sample_path(group: str, name: str) -> Path:
    if group in SCENARIOS:
        match = next((path for path in _scenario_paths(group) if path.name == name), None)
        if match is None:
            raise LookupError("örnek dosya bulunamadı")
        return match
    if group == "bicim":
        folder = FORMATS_DIR
    else:
        raise LookupError("örnek grubu bulunamadı")
    path = (folder / name).resolve()
    if path.parent != folder.resolve() or not path.is_file() or name.endswith(EXPECTED):
        raise LookupError("örnek dosya bulunamadı")
    return path


def scenario_files(scenario: str) -> list[Path]:
    if scenario not in SCENARIOS:
        raise LookupError("senaryo bulunamadı")
    return _scenario_paths(scenario)


# --------------------------------------------------------------------------- #
# Şablonlar
# --------------------------------------------------------------------------- #


def _xlsx(sheets: list[tuple[str, list[list[str]]]], widths: list[int],
          notes: dict[str, str] | None = None) -> bytes:
    """Şablon Excel'i. Açıklamalar ayrı sayfa değil başlık **notu** olarak eklenir: ayrı bir
    açıklama sayfası tanınmayan tablo olarak raporlanırdı."""
    import openpyxl
    from openpyxl.comments import Comment
    from openpyxl.styles import Font, PatternFill

    book = openpyxl.Workbook()
    book.remove(book.active)
    for title, rows in sheets:
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="2F5597")
            if notes and cell.value in notes:
                cell.comment = Comment(notes[cell.value], "DWHFlow")
        for index, width in enumerate(widths, start=1):
            sheet.column_dimensions[openpyxl.utils.get_column_letter(index)].width = width
        sheet.freeze_panes = "A2"
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def sttm_template() -> bytes:
    headers = ["Kaynak Sistem", "Kaynak Tablo", "Kaynak Kolon", "Dönüşüm Kuralı", "Hedef Tablo",
               "Hedef Kolon", "Job", "Yükleme Tipi"]
    rows = [headers,
            ["Core Banking", "SRC_MUSTERI", "MUSTERI_NO", "Doğrudan", "STG_MUSTERI", "MUSTERI_NO",
             "TPT_STG_MUSTERI", "Truncate-load"],
            ["", "STG_MUSTERI", "MUSTERI_NO", "Doğrudan", "ODS_MUSTERI", "MUSTERI_NO", "SP_ODS_MUSTERI",
             "Merge"]]
    notes = {
        "Kaynak Tablo": "Bir hücrede birden fazla tablo virgülle yazılabilir.",
        "Kaynak Kolon": "Kolon sütunları boş bırakılabilir; o zaman eşleme tablo düzeyinde olur.",
        "Job": "Akışı yapan job/prosedür/mapping adı. Boşsa bağ doğrudan (türetim) sayılır.",
        "Yükleme Tipi": "Full, Delta, Append, Merge, CDC, SCD1, SCD2, Truncate-load",
    }
    return _xlsx([("STTM", rows)], [16, 22, 18, 26, 22, 18, 22, 16], notes)


def jobs_template() -> bytes:
    headers = ["Job", "Önceki Job", "Kaynak Tablo", "Hedef Tablo", "Araç", "Başlangıç Saati"]
    rows = [headers,
            ["EXT_MUSTERI", "", "SRC_MUSTERI", "MUSTERI_YYYYMMDD.dat", "AutoSys", "00:30"],
            ["TPT_STG_MUSTERI", "EXT_MUSTERI", "MUSTERI_YYYYMMDD.dat", "STG_MUSTERI", "TPT", ""],
            ["SP_ODS_MUSTERI", "TPT_STG_MUSTERI", "STG_MUSTERI", "ODS_MUSTERI", "Teradata SP", ""]]
    return _xlsx([("Job Bağımlılıkları", rows)], [22, 22, 24, 24, 14, 16])


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx(blocks: list[tuple[str, Any]]) -> bytes:
    """En küçük geçerli .docx: başlıklar (Heading1/2), paragraflar ve tablolar."""
    body = []
    for kind, value in blocks:
        if kind in ("h1", "h2"):
            style = "Heading1" if kind == "h1" else "Heading2"
            body.append(f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r><w:t xml:space="preserve">'
                        f"{escape(value)}</w:t></w:r></w:p>")
        elif kind == "p":
            body.append(f'<w:p><w:r><w:t xml:space="preserve">{escape(value)}</w:t></w:r></w:p>')
        elif kind == "table":
            rows = "".join("<w:tr>" + "".join(
                f'<w:tc><w:p><w:r><w:t xml:space="preserve">{escape(cell)}</w:t></w:r></w:p></w:tc>'
                for cell in row) + "</w:tr>" for row in value)
            body.append('<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/>'
                        f"</w:tblPr>{rows}</w:tbl>")
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{_W_NS}">'
                f"<w:body>{''.join(body)}<w:sectPr/></w:body></w:document>")
    styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="{_W_NS}">'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
              '<w:basedOn w:val="Normal"/><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>'
              '<w:basedOn w:val="Normal"/><w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>'
              '<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/></w:style></w:styles>')
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/styles" Target="styles.xml"/></Relationships>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)
        archive.writestr("word/_rels/document.xml.rels", doc_rels)
    return buffer.getvalue()


def design_template() -> bytes:
    return _docx([
        ("h1", "1 Amaç ve kapsam"),
        ("p", "Bu doküman <iş alanı> verisinin kaynaktan rapora akışını anlatır. Aşağıdaki kalıplarla "
              "yazılan cümleler DWHFlow tarafından otomatik okunur."),
        ("h1", "2 Kaynaklar ve yükleme"),
        ("p", "SRC_MUSTERI tablosundan STG_MUSTERI tablosuna TPT_STG_MUSTERI job'ı ile yüklenir."),
        ("p", "STG_MUSTERI verisi SP_ODS_MUSTERI prosedürüyle ODS_MUSTERI tablosuna aktarılır."),
        ("p", "DWH.D_MUSTERI, ODS_MUSTERI tablosundan beslenir."),
        ("h1", "3 Eşleme tablosu"),
        ("table", [["Kaynak Tablo", "Hedef Tablo", "Job", "Yükleme Tipi"],
                   ["ODS_MUSTERI", "DWH.D_MUSTERI", "LOAD_D_MUSTERI", "SCD2"]]),
        ("h1", "4 Yazım önerileri"),
        ("p", "Tablo adlarını şemasıyla ya da şemasız, tutarlı yazın. Bir cümlede tek akış anlatın. "
              "Yön bildiren ekleri kullanın: -dan/-den (kaynak), -a/-e (hedef)."),
    ])


TEMPLATES: dict[str, dict[str, Any]] = {
    "sttm": {"name": "STTM_sablonu.xlsx", "title": "STTM / eşleme şablonu",
             "description": "Kaynak → hedef tablo ve kolon eşlemeleri, dönüşüm kuralı, job, yükleme tipi",
             "build": sttm_template},
    "joblar": {"name": "Job_bagimlilik_sablonu.xlsx", "title": "Job bağımlılık şablonu",
               "description": "Job, önceki job, okuduğu ve yazdığı tablo, araç, başlangıç saati",
               "build": jobs_template},
    "tasarim": {"name": "Tasarim_dokumani_sablonu.docx", "title": "Tasarım dokümanı şablonu",
                "description": "Otomatik okunan cümle kalıpları ve eşleme tablosuyla Word şablonu",
                "build": design_template},
}


def template(template_id: str) -> tuple[str, bytes]:
    entry = TEMPLATES.get(template_id)
    if entry is None:
        raise LookupError("şablon bulunamadı")
    return entry["name"], entry["build"]()

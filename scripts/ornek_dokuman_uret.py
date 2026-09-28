#!/usr/bin/env python3
"""Örnek dokümanları üretir: aynı akışın parçaları beş farklı biçime dağılmış.

    python scripts/ornek_dokuman_uret.py [hedef_dizin]

Senaryo — kredi kartı işlemleri:

    SRC_KART_ISLEM → KART_ISLEM_YYYYMMDD.dat → STG_KART_ISLEM → ODS_KART_ISLEM ─┐
    SRC_MUSTERI → STG_MUSTERI → ODS_MUSTERI → D_MUSTERI ──────────────────────────┤
                                                   F_KART_ISLEM → MART_KART_OZET → RPT_KART_PANO
                                                                               └→ EXP_BDDK_KART → BDDK_PORTAL

Bilerek dağınık: STTM Excel'de, anlatım Word'de, sorgular SQL'de, zincir JIL'de,
notlar TXT ve PDF'te. Word'de tanınmayacak bir tablo ve kuralın çözemeyeceği bir
paragraf da var — araç bunları gizlemeden raporlamalı.

Word ve PDF üretmek için ek kütüphane gerekmez (docx = zip içinde XML; PDF elle yazılır).
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "src" / "dwhflow" / "examples" / "ornek_dokumanlar"


def write_xlsx(path: Path) -> None:
    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "STTM"
    sheet.append(["Kart İşlemleri — Kaynaktan Hedefe Eşleme (v3)"])
    sheet.append([])
    sheet.append(["Kaynak Tablo", "Kaynak Kolon", "Hedef Tablo", "Hedef Kolon", "Dönüşüm Kuralı",
                  "Job", "Yükleme Tipi"])
    rows = [
        ("KART_ISLEM_YYYYMMDD.dat", "ISLEM_NO", "STG_KART_ISLEM", "ISLEM_ID", "doğrudan", "TPT_STG_KART", "truncate"),
        ("KART_ISLEM_YYYYMMDD.dat", "TUTAR", "STG_KART_ISLEM", "TUTAR", "COMP-3 çözülür", "TPT_STG_KART", "truncate"),
        ("STG_KART_ISLEM", "ISLEM_ID", "ODS_KART_ISLEM", "ISLEM_ID", "doğrudan", "SP_ODS_KART", "merge"),
        ("STG_KART_ISLEM", "TUTAR", "ODS_KART_ISLEM", "TUTAR", "TL'ye çevrilir", "SP_ODS_KART", "merge"),
        ("STG_MUSTERI", "MUSTERI_NO", "ODS_MUSTERI", "MUSTERI_ID", "doğrudan", "SP_ODS_MUSTERI", "delta"),
        ("ODS_MUSTERI", "MUSTERI_ID", "D_MUSTERI", "MUSTERI_ID", "doğrudan", "LOAD_D_MUSTERI", "SCD2"),
        ("ODS_KART_ISLEM", "TUTAR", "F_KART_ISLEM", "TUTAR_TL", "doğrudan", "LOAD_F_KART_ISLEM", "append"),
    ]
    for row in rows:
        sheet.append(list(row))
    deps = book.create_sheet("Job Bağımlılıkları")
    deps.append(["Job", "Önceki İş", "Araç"])
    for job, previous, tool in [
        ("TPT_STG_KART", "MFT_KART", "Teradata TPT"),
        ("SP_ODS_KART", "TPT_STG_KART", "Teradata SP"),
        ("SP_ODS_MUSTERI", "INF_STG_MUSTERI", "Teradata SP"),
        ("LOAD_D_MUSTERI", "SP_ODS_MUSTERI", "SQL"),
        ("LOAD_F_KART_ISLEM", "SP_ODS_KART, LOAD_D_MUSTERI", "SQL"),
    ]:
        deps.append([job, previous, tool])
    layout = book.create_sheet("Dosya Yerleşimi")  # kasıtlı: akış değil, alan yerleşimi
    layout.append(["Alan", "Tip", "Uzunluk", "Offset"])
    layout.append(["ISLEM_NO", "PIC X(16)", 16, 1])
    layout.append(["TUTAR", "PIC S9(13)V99 COMP-3", 8, 17])
    book.save(path)


_DOCX_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""
_DOCX_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>
</w:styles>"""


def _p(text: str, style: str | None = None) -> str:
    style_xml = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f'<w:p>{style_xml}<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def _table(rows: list[list[str]]) -> str:
    cells = "".join(
        "<w:tr>" + "".join(f"<w:tc>{_p(c)}</w:tc>" for c in row) + "</w:tr>" for row in rows
    )
    return f"<w:tbl>{cells}</w:tbl>"


def write_docx(path: Path) -> None:
    body = [
        _p("Kredi Kartı İşlemleri — Veri Ambarı Tasarım Dokümanı", "Heading1"),
        _p("1 Kaynaklar", "Heading2"),
        _p("Kart işlemleri çekirdek sistemde SRC_KART_ISLEM tablosunda tutulur. Her gece "
           "SRC_KART_ISLEM tablosundan KART_ISLEM_YYYYMMDD.dat dosyasına extract alınır."),
        _p("Müşteri bilgisi SRC_MUSTERI tablosundan STG_MUSTERI tablosuna ODBC ile aktarılır."),
        _p("2 Yükleme", "Heading2"),
        _p("Kart işlem dosyası KART_ISLEM_YYYYMMDD.dat MFT ile alındıktan sonra STG_KART_ISLEM "
           "tablosuna TPT ile yüklenir. D_MUSTERI tablosu ODS_MUSTERI tablosundan SCD2 ile beslenir."),
        _p("3 Mart katmanı", "Heading2"),
        _p("F_KART_ISLEM ve D_MUSTERI birleştirilerek MART_KART_OZET oluşturulur. Özet tablo "
           "segment ve gün kırılımında tutulur."),
        # Kuralın çözemeyeceği paragraf: iki tablo adı var ama yön kalıbı yok (LLM adayı).
        _p("Risk ekibinin sabah baktığı RPT_KART_PANO, MART_KART_OZET üzerinde çalışan gece "
           "raporlamasının çıktısıdır."),
        _p("4 Tablo sözlüğü", "Heading2"),
        # Kasıtlı: akış tablosu değil, sözlük. Araç bunu "tanınmadı" diye raporlamalı.
        _table([["Tablo", "Sahip", "Saklama"],
                ["F_KART_ISLEM", "DWH Ekibi", "10 yıl"],
                ["MART_KART_OZET", "Raporlama Ekibi", "3 yıl"]]),
    ]
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f'<w:body>{"".join(body)}</w:body></w:document>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _DOCX_TYPES)
        archive.writestr("_rels/.rels", _DOCX_RELS)
        archive.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        archive.writestr("word/styles.xml", _STYLES)
        archive.writestr("word/document.xml", document)


SQL = """-- Kart işlemleri mart yüklemesi (Teradata)
.LOGON tdprod/etl_user,$PASSWORD;

INSERT INTO DWH.F_KART_ISLEM (ISLEM_ID, MUSTERI_KEY, ISLEM_TARIHI, TUTAR_TL)
SELECT k.ISLEM_ID, m.MUSTERI_KEY, k.ISLEM_TARIHI, k.TUTAR
FROM ODS_KART_ISLEM k
JOIN DWH.D_MUSTERI m ON m.MUSTERI_ID = k.MUSTERI_ID
WHERE k.DURUM = 'A';

CREATE TABLE DWH.MART_KART_OZET AS (
  SELECT m.SEGMENT, f.ISLEM_TARIHI, SUM(f.TUTAR_TL) AS TOPLAM_TUTAR
  FROM DWH.F_KART_ISLEM f
  JOIN DWH.D_MUSTERI m ON m.MUSTERI_KEY = f.MUSTERI_KEY
  GROUP BY m.SEGMENT, f.ISLEM_TARIHI
) WITH DATA;

INSERT INTO EXP_BDDK_KART
SELECT SEGMENT, ISLEM_TARIHI, TOPLAM_TUTAR FROM DWH.MART_KART_OZET;

.LOGOFF;
"""

JIL = """/* Kart işlemleri gece zinciri */
insert_job: BOX_KART_GECE   job_type: BOX
start_times: "01:00"
days_of_week: all

insert_job: EXT_KART   job_type: CMD
box_name: BOX_KART_GECE
command: /etl/bin/ext_kart.sh
description: "Kart islem extract"

insert_job: MFT_KART   job_type: CMD
box_name: BOX_KART_GECE
condition: s(EXT_KART)

insert_job: TPT_STG_KART   job_type: CMD
box_name: BOX_KART_GECE
condition: s(MFT_KART)

insert_job: SP_ODS_KART   job_type: CMD
condition: s(TPT_STG_KART)

insert_job: LOAD_F_KART_ISLEM   job_type: CMD
condition: s(SP_ODS_KART) & s(LOAD_D_MUSTERI)

insert_job: LOAD_MART_KART_OZET   job_type: CMD
condition: s(LOAD_F_KART_ISLEM)

insert_job: EXP_BDDK   job_type: CMD
condition: s(LOAD_MART_KART_OZET)
"""

NOTES = """# Operasyon notları

Müşteri hattı: SRC_MUSTERI -> STG_MUSTERI -> ODS_MUSTERI -> D_MUSTERI

- STG_KART_ISLEM'den ODS_KART_ISLEM'e SP_ODS_KART prosedürüyle merge yapılır.
- Gece zinciri 01:00'de başlar, 06:00'ya kadar bitmelidir.
"""

PDF_LINES = [
    "BDDK Bildirim Sureci",
    "",
    "EXP_BDDK_KART dosyasi her sabah MFT ile BDDK_PORTAL sistemine gonderilir.",
    "Gonderim oncesi toplam tutar MART_KART_OZET ile mutabakat edilir.",
]


def write_pdf(path: Path, lines: list[str]) -> None:
    """Tek sayfalık, yalnız ASCII metinli en küçük geçerli PDF."""
    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    content_lines = ["BT", "/F1 11 Tf", "14 TL", "56 780 Td"]
    for line in lines:
        content_lines.append(f"({esc(line)}) Tj T*")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))


def generate(out: Path = DEFAULT_OUT) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "STTM_Kart_Islemleri.xlsx": write_xlsx,
        "Kart_Tasarim_Dokumani.docx": write_docx,
    }
    written = []
    for name, writer in files.items():
        writer(out / name)
        written.append(out / name)
    for name, text in {"kart_mart_yukleme.sql": SQL, "kart_gece_zinciri.jil": JIL,
                       "operasyon_notlari.txt": NOTES}.items():
        (out / name).write_text(text, encoding="utf-8")
        written.append(out / name)
    write_pdf(out / "BDDK_Bildirim_Sureci.pdf", PDF_LINES)
    written.append(out / "BDDK_Bildirim_Sureci.pdf")
    return written


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    for file in generate(target):
        print(f"yazıldı: {file}")

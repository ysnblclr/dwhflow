#!/usr/bin/env python3
"""Biçim örneklerinin ikili dosyalarını üretir (deterministik; ek kütüphane gerekmez).

    python scripts/ornek_bicim_uret.py

Yazılanlar:

* ``examples/ornek_bicimler/``: ``.xls`` (OLE2 + BIFF8), ``.doc`` (OLE2 + Word 97 FIB ve parça
  tablosu), ``.pptx``, ``.odt``, ``.ods``, ``.vsdx``, sıkıştırılmış draw.io (``.xml``), ``.eml``
  ve ``.zip`` örnekleri — hepsi kredi kartı senaryosunun adlarını kullanır.
* ``examples/ornek_zor/``: bozuk ``.docx``, zip-slip ``..`` yolu, iç içe zip, zip bombası taklidi,
  şifreli öğe bayraklı zip ve ekli/iletilmiş e-posta.

Metin tabanlı örnekler (RTF, HTML, draw.io, BPMN, Mermaid, DOT, şablonlar) elle yazılmıştır ve
depoda durur. Zip'ler sıkıştırmasız (``ZIP_STORED``) ve sabit tarihli yazılır: aynı girdi her
makinede aynı baytları verir (yalnız zip bombası taklidi ve sıkıştırılmış draw.io sayfası
deflate kullanır; bunların baytları zlib sürümüne göre değişebilir, içerikleri değişmez).

``.xls`` için xlwt gerekmez: OLE2 (Compound File) kabı ve BIFF8 kayıtları burada yazılır;
akışlar 4096 bayta tamamlanır, böylece mini akış tablosu gerekmez. ``.doc`` aynı kapla,
yalnız metin (FIB + CLX parça tablosu, UTF-16) içerir; biçim/stil bilgisi yoktur.
"""

from __future__ import annotations

import base64
import struct
import sys
import zipfile
import zlib
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "src" / "dwhflow" / "examples"
FORMATS = EXAMPLES / "ornek_bicimler"
HARD = EXAMPLES / "ornek_zor"
DATE = (2026, 1, 15, 9, 0, 0)

# --------------------------------------------------------------------------- #
# Zip
# --------------------------------------------------------------------------- #


def zip_bytes(entries: list[tuple[str, bytes]], compression: int = zipfile.ZIP_STORED) -> bytes:
    """Deterministik zip: sabit tarih, sabit sistem kodu, verilen sıra."""
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=DATE)
            info.create_system = 3
            info.external_attr = (0o40755 << 16 | 0x10) if name.endswith("/") else 0o644 << 16
            info.compress_type = compression
            archive.writestr(info, data)
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# OLE2 / Compound File Binary (sürüm 3, 512 bayt kesim)
# --------------------------------------------------------------------------- #

CFB_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_SECTOR = 512
_END, _FREE, _FATSECT, _NONE = 0xFFFFFFFE, 0xFFFFFFFF, 0xFFFFFFFD, 0xFFFFFFFF


def _dir_entry(name: str, kind: int, start: int = 0, size: int = 0, left: int = _NONE, right: int = _NONE,
               child: int = _NONE, color: int = 1) -> bytes:
    encoded = (name + "\0").encode("utf-16-le") if name else b""
    return struct.pack("<64sHBBIII16sIQQIQ", encoded, len(encoded), kind, color, left, right, child,
                       b"\0" * 16, 0, 0, 0, start, size)


def cfb(streams: list[tuple[str, bytes]]) -> bytes:
    """Kök altında düz akışlar. Akış sırası ada göre (uzunluk, büyük harf) sıralı verilmelidir."""
    sectors: list[bytes] = []
    placed = []
    for name, payload in streams:
        payload = payload.ljust(4096, b"\0")  # mini akış eşiğinin altında kalmasın
        count = -(-len(payload) // _SECTOR)
        placed.append((name, len(sectors), len(payload), count))
        padded = payload.ljust(count * _SECTOR, b"\0")
        sectors += [padded[i * _SECTOR:(i + 1) * _SECTOR] for i in range(count)]
    # Dizin: kök + akışlar; akışlar sağa doğru zincir (ikili arama ağacı, kök siyah, diğerleri kırmızı)
    entries = [_dir_entry("Root Entry", 5, _END, 0, child=1 if streams else _NONE)]
    for index, (name, start, size, _) in enumerate(placed, start=1):
        right = index + 1 if index < len(placed) else _NONE
        entries.append(_dir_entry(name, 2, start, size, right=right, color=1 if index == 1 else 0))
    while len(entries) % 4:
        entries.append(_dir_entry("", 0))
    directory = b"".join(entries)
    dir_start, dir_count = len(sectors), len(directory) // _SECTOR
    sectors += [directory[i * _SECTOR:(i + 1) * _SECTOR] for i in range(dir_count)]
    fat_count = 1
    while fat_count * 128 < len(sectors) + fat_count:
        fat_count += 1
    fat_start = len(sectors)
    fat = [_FREE] * (fat_count * 128)
    for _, start, _, count in placed:
        for i in range(count):
            fat[start + i] = start + i + 1 if i < count - 1 else _END
    for i in range(dir_count):
        fat[dir_start + i] = dir_start + i + 1 if i < dir_count - 1 else _END
    for i in range(fat_count):
        fat[fat_start + i] = _FATSECT
    table = struct.pack(f"<{len(fat)}I", *fat)
    sectors += [table[i * _SECTOR:(i + 1) * _SECTOR] for i in range(fat_count)]
    difat = [fat_start + i for i in range(fat_count)] + [_FREE] * (109 - fat_count)
    header = struct.pack("<8s16sHHHHH6sIIIIIIIII", CFB_SIGNATURE, b"\0" * 16, 0x003E, 0x0003, 0xFFFE, 9, 6,
                         b"\0" * 6, 0, fat_count, dir_start, 0, 4096, _END, 0, _END, 0)
    header += struct.pack("<109I", *difat)
    return header + b"".join(sectors)


# --------------------------------------------------------------------------- #
# Excel 97-2003 (BIFF8)
# --------------------------------------------------------------------------- #


def _rec(kind: int, data: bytes = b"") -> bytes:
    return struct.pack("<HH", kind, len(data)) + data


def _short_string(text: str) -> bytes:
    return struct.pack("<BB", len(text), 1) + text.encode("utf-16-le")


def xls_bytes(sheets: list[tuple[str, list[list[object]]]]) -> bytes:
    """Metin (paylaşılan dizge) ve sayı hücreli BIFF8 çalışma kitabı."""
    strings: list[str] = []
    index: dict[str, int] = {}
    for _, rows in sheets:
        for row in rows:
            for value in row:
                if isinstance(value, str) and value not in index:
                    index[value] = len(strings)
                    strings.append(value)
    total = sum(isinstance(v, str) for _, rows in sheets for row in rows for v in row)
    sst = struct.pack("<II", total, len(strings)) + b"".join(
        struct.pack("<HB", len(s), 1) + s.encode("utf-16-le") for s in strings)
    if len(sst) > 8224:
        raise ValueError("SST CONTINUE kaydı gerektirecek kadar büyük; örneği küçültün")
    bof_globals = _rec(0x0809, struct.pack("<HHHHII", 0x0600, 0x0005, 0x0DBB, 0x07CC, 0x41, 0x06))
    font = _rec(0x0031, struct.pack("<HHHHHBBBB", 200, 0, 0x7FFF, 400, 0, 0, 0, 0, 0) + _short_string("Arial"))
    style_xf = _rec(0x00E0, struct.pack("<HHHBBBBIIH", 0, 0, 0xFFF5, 0x20, 0, 0, 0xF4, 0, 0, 0x20C0))
    cell_xf = _rec(0x00E0, struct.pack("<HHHBBBBIIH", 0, 0, 0x0001, 0x20, 0, 0, 0x00, 0, 0, 0x20C0))
    head = (bof_globals + _rec(0x0042, struct.pack("<H", 1200))
            + _rec(0x003D, struct.pack("<HHHHHHHHH", 0, 0, 0x3000, 0x2000, 0x38, 0, 0, 1, 0x258))
            + font * 5 + style_xf * 15 + cell_xf + _rec(0x0293, struct.pack("<HBB", 0x8000, 0, 0xFF)))
    tail = _rec(0x00FC, sst) + _rec(0x000A)

    def sheet_stream(rows: list[list[object]]) -> bytes:
        cells = b""
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                if isinstance(value, str):
                    cells += _rec(0x00FD, struct.pack("<HHHI", r, c, 15, index[value]))
                elif isinstance(value, (int, float)):
                    cells += _rec(0x0203, struct.pack("<HHHd", r, c, 15, float(value)))
        width = max((len(row) for row in rows), default=0)
        return (_rec(0x0809, struct.pack("<HHHHII", 0x0600, 0x0010, 0x0DBB, 0x07CC, 0x41, 0x06))
                + _rec(0x0200, struct.pack("<IIHHH", 0, len(rows), 0, width, 0)) + cells
                + _rec(0x023E, struct.pack("<HHHHHHHI", 0x06B6, 0, 0, 0x40, 0, 0, 0, 0)) + _rec(0x000A))

    bodies = [sheet_stream(rows) for _, rows in sheets]

    def boundsheets(offsets: list[int]) -> bytes:
        return b"".join(_rec(0x0085, struct.pack("<IBB", offset, 0, 0) + _short_string(name))
                        for (name, _), offset in zip(sheets, offsets, strict=True))

    globals_size = len(head) + len(boundsheets([0] * len(sheets))) + len(tail)
    offsets, position = [], globals_size
    for body in bodies:
        offsets.append(position)
        position += len(body)
    workbook = head + boundsheets(offsets) + tail + b"".join(bodies)
    return cfb([("Workbook", workbook)])


# --------------------------------------------------------------------------- #
# Word 97-2003 (yalnız metin: FIB + parça tablosu)
# --------------------------------------------------------------------------- #


def doc_bytes(paragraphs: list[str]) -> bytes:
    text = "\r".join(paragraphs) + "\r"
    encoded = text.encode("utf-16-le")
    fc_text = 0x400
    word = bytearray(fc_text + len(encoded))
    struct.pack_into("<HHHHHH", word, 0, 0xA5EC, 0x00C1, 0, 0x041F, 0, 0x1200)  # 1Table + fExtChar
    struct.pack_into("<H", word, 0x0C, 0x00BF)
    struct.pack_into("<II", word, 0x18, fc_text, fc_text + len(encoded))  # fcMin, fcMac
    struct.pack_into("<H", word, 0x20, 14)  # csw
    struct.pack_into("<H", word, 0x3E, 22)  # cslw
    struct.pack_into("<i", word, 0x40, len(word))  # cbMac
    struct.pack_into("<i", word, 0x40 + 12, len(text))  # ccpText
    struct.pack_into("<H", word, 0x98, 93)  # cbRgFcLcb (Word 97)
    plc = struct.pack("<II", 0, len(text)) + struct.pack("<HIH", 0, fc_text, 0)
    clx = b"\x02" + struct.pack("<I", len(plc)) + plc
    struct.pack_into("<II", word, 0x9A + 33 * 8, 0, len(clx))  # fcClx, lcbClx
    word[fc_text:] = encoded
    return cfb([("1Table", clx), ("WordDocument", bytes(word))])


# --------------------------------------------------------------------------- #
# PowerPoint
# --------------------------------------------------------------------------- #

_PML = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')
_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
_OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def _rels(items: list[tuple[str, str, str]]) -> bytes:
    """(kimlik, tür, hedef); tür kısa ad (Office ilişkisi) ya da tam URI olabilir."""
    body = "".join(f'<Relationship Id="{rid}" Type="{kind if "://" in kind else f"{_OFFICE_REL}/{kind}"}" '
                   f'Target="{target}"/>' for rid, kind, target in items)
    return f'{_XML}<Relationships xmlns="{_RELS}">{body}</Relationships>'.encode()


def _sp(shape_id: int, name: str, paragraphs: list[str], placeholder: str | None = None) -> str:
    ph = f'<p:nvPr><p:ph type="{placeholder}"/></p:nvPr>' if placeholder else "<p:nvPr/>"
    paras = "".join(f'<a:p><a:r><a:rPr lang="tr-TR"/><a:t>{escape(t)}</a:t></a:r></a:p>' for t in paragraphs)
    body = f"<p:txBody><a:bodyPr/><a:lstStyle/>{paras}</p:txBody>" if paragraphs else ""
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="{escape(name)}"/><p:cNvSpPr/>{ph}</p:nvSpPr>'
            f"<p:spPr/>{body}</p:sp>")


def _table_frame(shape_id: int, rows: list[list[str]]) -> str:
    grid = "".join('<a:gridCol w="2438400"/>' for _ in rows[0])
    body = "".join("<a:tr h=\"370840\">" + "".join(
        f'<a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>{escape(c)}</a:t></a:r></a:p></a:txBody>'
        '<a:tcPr/></a:tc>' for c in row) + "</a:tr>" for row in rows)
    return (f'<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="{shape_id}" name="Tablo {shape_id}"/>'
            '<p:cNvGraphicFramePr><a:graphicFrameLocks noGrp="1"/></p:cNvGraphicFramePr><p:nvPr/>'
            '</p:nvGraphicFramePr><p:xfrm><a:off x="457200" y="2286000"/><a:ext cx="7315200" cy="1112520"/>'
            '</p:xfrm><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">'
            f'<a:tbl><a:tblPr firstRow="1"/><a:tblGrid>{grid}</a:tblGrid>{body}</a:tbl>'
            '</a:graphicData></a:graphic></p:graphicFrame>')


def _tree(shapes: str) -> str:
    return ('<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            f'<p:grpSpPr/>{shapes}</p:spTree></p:cSld>')


_THEME = (_XML + '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Ofis">'
          '<a:themeElements><a:clrScheme name="Ofis"><a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
          '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="1F497D"/></a:dk2>'
          '<a:lt2><a:srgbClr val="EEECE1"/></a:lt2><a:accent1><a:srgbClr val="4F81BD"/></a:accent1>'
          '<a:accent2><a:srgbClr val="C0504D"/></a:accent2><a:accent3><a:srgbClr val="9BBB59"/></a:accent3>'
          '<a:accent4><a:srgbClr val="8064A2"/></a:accent4><a:accent5><a:srgbClr val="4BACC6"/></a:accent5>'
          '<a:accent6><a:srgbClr val="F79646"/></a:accent6><a:hlink><a:srgbClr val="0000FF"/></a:hlink>'
          '<a:folHlink><a:srgbClr val="800080"/></a:folHlink></a:clrScheme><a:fontScheme name="Ofis">'
          '<a:majorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
          '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont>'
          '</a:fontScheme><a:fmtScheme name="Ofis"><a:fillStyleLst>'
          + '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>' * 3 + '</a:fillStyleLst><a:lnStyleLst>'
          + '<a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>' * 3
          + '</a:lnStyleLst><a:effectStyleLst>' + '<a:effectStyle><a:effectLst/></a:effectStyle>' * 3
          + '</a:effectStyleLst><a:bgFillStyleLst>' + '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>' * 3
          + '</a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>')


def pptx_bytes(slides: list[dict]) -> bytes:
    """slide: {"title", "paragraphs", "table" (satırlar ya da None), "notes"}."""
    count = len(slides)
    overrides = ['<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-'
                 'officedocument.presentationml.presentation.main+xml"/>',
                 '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.'
                 'openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
                 '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.'
                 'openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
                 '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-'
                 'officedocument.theme+xml"/>']
    for n in range(1, count + 1):
        overrides.append(f'<Override PartName="/ppt/slides/slide{n}.xml" ContentType="application/vnd.'
                         'openxmlformats-officedocument.presentationml.slide+xml"/>')
        if slides[n - 1].get("notes"):
            overrides.append(f'<Override PartName="/ppt/notesSlides/notesSlide{n}.xml" ContentType="application/'
                             'vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>')
    types = (f'{_XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             f'<Default Extension="xml" ContentType="application/xml"/>{"".join(overrides)}</Types>')
    # Slayt kimlikleri bilerek ters sırada dosyalanır: okuyucu sırayı presentation.xml'den almalı.
    slide_ids = "".join(f'<p:sldId id="{256 + n}" r:id="rId{10 + n}"/>' for n in range(1, count + 1))
    presentation = (f'{_XML}<p:presentation {_PML}><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/>'
                    f'</p:sldMasterIdLst><p:sldIdLst>{slide_ids}</p:sldIdLst><p:sldSz cx="9144000" cy="6858000"/>'
                    '<p:notesSz cx="6858000" cy="9144000"/></p:presentation>')
    pres_rels = [("rId1", "slideMaster", "slideMasters/slideMaster1.xml"), ("rId2", "theme", "theme/theme1.xml")]
    pres_rels += [(f"rId{10 + n}", "slide", f"slides/slide{count + 1 - n}.xml") for n in range(1, count + 1)]
    master = (f'{_XML}<p:sldMaster {_PML}>{_tree("")}<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" '
              'accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" '
              'accent6="accent6" hlink="hlink" folHlink="folHlink"/><p:sldLayoutIdLst>'
              '<p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst></p:sldMaster>')
    layout = (f'{_XML}<p:sldLayout {_PML} type="obj">{_tree("")}'
              "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>")
    entries = [("[Content_Types].xml", types.encode()),
               ("_rels/.rels", _rels([("rId1", "officeDocument", "ppt/presentation.xml")])),
               ("ppt/presentation.xml", presentation.encode()),
               ("ppt/_rels/presentation.xml.rels", _rels(pres_rels)),
               ("ppt/slideMasters/slideMaster1.xml", master.encode()),
               ("ppt/slideMasters/_rels/slideMaster1.xml.rels",
                _rels([("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"),
                       ("rId2", "theme", "../theme/theme1.xml")])),
               ("ppt/slideLayouts/slideLayout1.xml", layout.encode()),
               ("ppt/slideLayouts/_rels/slideLayout1.xml.rels",
                _rels([("rId1", "slideMaster", "../slideMasters/slideMaster1.xml")])),
               ("ppt/theme/theme1.xml", _THEME.encode())]
    for position, slide in enumerate(slides, start=1):
        number = count + 1 - position  # dosya adı sırası sunum sırasının tersi
        shapes = _sp(2, "Başlık 1", [slide["title"]], "title")
        if slide.get("paragraphs"):
            shapes += _sp(3, "İçerik 2", slide["paragraphs"], "body")
        if slide.get("table"):
            shapes += _table_frame(4, slide["table"])
        shapes += _sp(5, "Slayt Numarası 3", [str(position)], "sldNum")
        entries.append((f"ppt/slides/slide{number}.xml",
                        f'{_XML}<p:sld {_PML}>{_tree(shapes)}<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>'
                        '</p:sld>'.encode()))
        rels = [("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml")]
        if slide.get("notes"):
            rels.append(("rId2", "notesSlide", f"../notesSlides/notesSlide{number}.xml"))
            notes = _sp(2, "Slayt Görüntüsü 1", [], "sldImg") + _sp(3, "Not 2", slide["notes"], "body")
            entries.append((f"ppt/notesSlides/notesSlide{number}.xml",
                            f'{_XML}<p:notes {_PML}>{_tree(notes)}</p:notes>'.encode()))
            entries.append((f"ppt/notesSlides/_rels/notesSlide{number}.xml.rels",
                            _rels([("rId1", "slide", f"../slides/slide{number}.xml")])))
        entries.append((f"ppt/slides/_rels/slide{number}.xml.rels", _rels(rels)))
    return zip_bytes(entries)


# --------------------------------------------------------------------------- #
# OpenDocument
# --------------------------------------------------------------------------- #

_ODF_NS = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
           'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
           'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" office:version="1.3"')


def odf_bytes(mimetype: str, body: str) -> bytes:
    content = f'{_XML}<office:document-content {_ODF_NS}><office:body>{body}</office:body></office:document-content>'
    manifest = (f'{_XML}<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
                'manifest:version="1.3"><manifest:file-entry manifest:full-path="/" '
                f'manifest:media-type="{mimetype}"/><manifest:file-entry manifest:full-path="content.xml" '
                'manifest:media-type="text/xml"/></manifest:manifest>')
    return zip_bytes([("mimetype", mimetype.encode()), ("META-INF/manifest.xml", manifest.encode()),
                      ("content.xml", content.encode())])


def _odf_cells(row: list[str]) -> str:
    return "".join(f'<table:table-cell office:value-type="string"><text:p>{escape(c)}</text:p></table:table-cell>'
                   if c else "<table:table-cell/>" for c in row)


def odt_table(name: str, rows: list[list[str]]) -> str:
    body = "".join(f"<table:table-row>{_odf_cells(row)}</table:table-row>" for row in rows)
    return (f'<table:table table:name="{escape(name)}"><table:table-column table:number-columns-repeated='
            f'"{len(rows[0])}"/>{body}</table:table>')


# --------------------------------------------------------------------------- #
# Visio
# --------------------------------------------------------------------------- #

_VNS = "http://schemas.microsoft.com/office/visio/2012/main"
_VREL = "http://schemas.microsoft.com/visio/2010/relationships"


def _vshape(shape_id: int, master: int, text: str, x: float, y: float) -> str:
    return (f'<Shape ID="{shape_id}" Type="Shape" Master="{master}"><Cell N="PinX" V="{x}"/>'
            f'<Cell N="PinY" V="{y}"/><Cell N="Width" V="1.5"/><Cell N="Height" V="0.75"/>'
            f'<Text>{escape(text)}</Text></Shape>')


def _vconnector(shape_id: int, master: int, text: str = "", begin_arrow: int = 0, end_arrow: int = 13) -> str:
    label = f"<Text>{escape(text)}</Text>" if text else ""
    return (f'<Shape ID="{shape_id}" Type="Shape" Master="{master}"><Cell N="BeginX" V="0"/><Cell N="EndX" V="1"/>'
            f'<Cell N="BeginArrow" V="{begin_arrow}"/><Cell N="EndArrow" V="{end_arrow}"/>{label}</Shape>')


def vsdx_bytes(pages: list[dict], masters: dict[int, str]) -> bytes:
    """page: {"name", "background", "shapes" (XML), "connects": [(bağlayıcı, başlangıç, bitiş)]}."""
    page_list = "".join(
        f'<Page ID="{n}" NameU="{escape(p["name"])}" Name="{escape(p["name"])}"'
        f'{" Background=" + chr(34) + "1" + chr(34) if p.get("background") else ""}>'
        f'<PageSheet/><Rel r:id="rId{n}"/></Page>' for n, p in enumerate(pages, start=1))
    rel_ns = f'xmlns:r="{_OFFICE_REL}"'
    entries = [
        ("[Content_Types].xml", (f'{_XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                                 '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
                                 'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                                 '<Override PartName="/visio/document.xml" ContentType="application/vnd.ms-visio.'
                                 'drawing.main+xml"/></Types>').encode()),
        ("_rels/.rels", _rels([("rId1", f"{_VREL}/document", "visio/document.xml")])),
        ("visio/document.xml", f'{_XML}<VisioDocument xmlns="{_VNS}" {rel_ns}/>'.encode()),
        ("visio/_rels/document.xml.rels", _rels([("rId1", f"{_VREL}/pages", "pages/pages.xml"),
                                                  ("rId2", f"{_VREL}/masters", "masters/masters.xml")])),
        ("visio/masters/masters.xml", (f'{_XML}<Masters xmlns="{_VNS}" {rel_ns}>' + "".join(
            f'<Master ID="{mid}" NameU="{escape(name)}" Name="{escape(name)}"><Rel r:id="rId{mid}"/></Master>'
            for mid, name in masters.items()) + "</Masters>").encode()),
        ("visio/masters/_rels/masters.xml.rels", _rels([(f"rId{mid}", f"{_VREL}/master", f"master{mid}.xml")
                                                         for mid in masters])),
        ("visio/pages/pages.xml", f'{_XML}<Pages xmlns="{_VNS}" {rel_ns}>{page_list}</Pages>'.encode()),
        ("visio/pages/_rels/pages.xml.rels", _rels([(f"rId{n}", f"{_VREL}/page", f"page{n}.xml")
                                                     for n in range(1, len(pages) + 1)])),
    ]
    for mid in masters:
        entries.append((f"visio/masters/master{mid}.xml",
                        f'{_XML}<MasterContents xmlns="{_VNS}"><Shapes/></MasterContents>'.encode()))
    for n, page in enumerate(pages, start=1):
        connects = "".join(
            f'<Connect FromSheet="{c}" FromCell="BeginX" FromPart="9" ToSheet="{a}" ToCell="PinX" ToPart="3"/>'
            f'<Connect FromSheet="{c}" FromCell="EndX" FromPart="12" ToSheet="{b}" ToCell="PinX" ToPart="3"/>'
            for c, a, b in page.get("connects", []))
        entries.append((f"visio/pages/page{n}.xml",
                        (f'{_XML}<PageContents xmlns="{_VNS}" {rel_ns}><Shapes>{page["shapes"]}</Shapes>'
                         f'<Connects>{connects}</Connects></PageContents>').encode()))
    return zip_bytes(entries)


# --------------------------------------------------------------------------- #
# draw.io sıkıştırma ve e-posta
# --------------------------------------------------------------------------- #


def drawio_compress(model_xml: str) -> str:
    """draw.io'nun sayfa sıkıştırması: encodeURIComponent → ham deflate → base64."""
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    raw = compressor.compress(quote(model_xml, safe="~()*!.'").encode("ascii")) + compressor.flush()
    return base64.b64encode(raw).decode("ascii")


def _mime_word(text: str) -> str:
    """RFC 2047 kodlu başlık sözcüğü (ASCII dışı ad)."""
    return f"=?utf-8?b?{base64.b64encode(text.encode('utf-8')).decode('ascii')}?="


def _b64(data: bytes) -> str:
    text = base64.b64encode(data).decode("ascii")
    return "\n".join(text[i:i + 76] for i in range(0, len(text), 76))


def eml_bytes(headers: dict[str, str], body: str, attachments: list[tuple[str, str, bytes]],
              boundary: str = "=_dwhflow_ornek_0") -> bytes:
    """attachments: (ad, içerik türü, veri). ``message/rfc822`` ekleri olduğu gibi gömülür."""
    lines = [f"{key}: {value}" for key, value in headers.items()]
    lines += ["MIME-Version: 1.0", f'Content-Type: multipart/mixed; boundary="{boundary}"', "",
              "Bu çok parçalı bir MIME iletisidir.", "", f"--{boundary}",
              "Content-Type: text/plain; charset=utf-8", "Content-Transfer-Encoding: base64", "", _b64(body.encode())]
    for name, content_type, data in attachments:
        lines.append(f"--{boundary}")
        if content_type == "message/rfc822":
            lines += ["Content-Type: message/rfc822", f'Content-Disposition: attachment; filename="{name}"', "",
                      data.decode("utf-8")]
            continue
        disposition = "inline" if content_type.startswith("image/") else "attachment"
        lines += [f'Content-Type: {content_type}; name="{name}"', "Content-Transfer-Encoding: base64",
                  f'Content-Disposition: {disposition}; filename="{name}"', "", _b64(data)]
    lines += [f"--{boundary}--", ""]
    return "\r\n".join(lines).encode("utf-8")


_PNG_1X1 = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEA"
                            "HnOcQAAAAABJRU5ErkJggg==")

# --------------------------------------------------------------------------- #
# Örnek içerikleri (kredi kartı senaryosu)
# --------------------------------------------------------------------------- #

XLS_SHEETS: list[tuple[str, list[list[object]]]] = [
    ("STTM", [
        ["Kart İşlemleri — Kaynaktan Hedefe Eşleme (Excel 97-2003)"],
        [],
        ["Kaynak Tablo", "Hedef Tablo", "Job", "Yükleme Tipi", "Sürüm"],
        ["SRC_KART_ISLEM", "KART_ISLEM_YYYYMMDD.dat", "EXT_KART", "full", 1],
        ["KART_ISLEM_YYYYMMDD.dat", "STG_KART_ISLEM", "TPT_STG_KART", "truncate", 1],
        ["STG_KART_ISLEM", "ODS_KART_ISLEM", "SP_ODS_KART", "merge", 2],
        ["SRC_MUSTERI", "STG_MUSTERI", None, "full", 1],
        ["ODS_KART_ISLEM", "DWH.F_KART_ISLEM", "LOAD_F_KART_ISLEM", "append", 3],
    ]),
    ("Job Bağımlılıkları", [
        ["Job", "Önceki İş"],
        ["TPT_STG_KART", "MFT_KART"],
        ["SP_ODS_KART", "TPT_STG_KART"],
        ["LOAD_F_KART_ISLEM", "SP_ODS_KART"],
    ]),
    ("Notlar", [
        ["DWH.D_MUSTERI tablosu ODS_MUSTERI tablosundan LOAD_D_MUSTERI işiyle beslenir."],
        ["Sorumlu: DWH ekibi"],
    ]),
]

DOC_PARAGRAPHS = [
    "Kredi Kartı İşlemleri — Tasarım Dokümanı (Word 97-2003)",
    "1 Kaynaklar",
    "Her gece SRC_KART_ISLEM tablosundan KART_ISLEM_YYYYMMDD.dat dosyasına EXT_KART işiyle extract alınır.",
    "2 Yükleme",
    "KART_ISLEM_YYYYMMDD.dat dosyası TPT_STG_KART işiyle STG_KART_ISLEM tablosuna yüklenir.",
    "STG_KART_ISLEM tablosundan ODS_KART_ISLEM tablosuna SP_ODS_KART prosedürüyle aktarılır.",
    "Gece zinciri 01:00'de başlar ve 06:00'ya kadar bitmelidir.",
]

PPTX_SLIDES = [
    {"title": "Kart İşlemleri Veri Ambarı Mimarisi",
     "paragraphs": ["Mart katmanı ve BDDK bildirimi", "Sürüm 2026.1"]},
    {"title": "Mart katmanı",
     "paragraphs": ["ODS_KART_ISLEM tablosundan DWH.F_KART_ISLEM tablosuna LOAD_F_KART_ISLEM işiyle yüklenir.",
                    "Özet tablo gün ve segment kırılımında tutulur."],
     "table": [["Kaynak Tablo", "Hedef Tablo", "Job"],
               ["DWH.F_KART_ISLEM", "DWH.MART_KART_OZET", "LOAD_MART_KART_OZET"],
               ["DWH.D_MUSTERI", "DWH.MART_KART_OZET", "LOAD_MART_KART_OZET"]],
     "notes": ["Konuşmacı notu: DWH.MART_KART_OZET tablosundan EXP_BDDK_KART dosyasına EXP_BDDK işiyle aktarılır.",
               "Soru gelirse gönderim saatini söyle."]},
]

ODT_BODY = (
    "<office:text>"
    '<text:h text:outline-level="1">Müşteri hattı</text:h>'
    "<text:p>SRC_MUSTERI tablosundan STG_MUSTERI tablosuna aktarılır.</text:p>"
    "<text:list><text:list-item><text:p>STG_MUSTERI tablosundan ODS_MUSTERI tablosuna<text:s/>yüklenir."
    "</text:p></text:list-item></text:list>"
    '<text:p>Ayrıntı için dipnota bakın.<text:note text:note-class="footnote">'
    "<text:note-citation>1</text:note-citation><text:note-body><text:p>Eski not: HATALI_NOT tablosundan YANLIS_HEDEF tablosuna yüklenir.</text:p>"
    "</text:note-body></text:note></text:p>"
    '<text:h text:outline-level="1">Boyut yüklemesi</text:h>'
    + odt_table("Tablo1", [["Kaynak Tablo", "Hedef Tablo", "Job"], ["ODS_MUSTERI", "DWH.D_MUSTERI", "LOAD_D_MUSTERI"]])
    + '<text:tracked-changes><text:changed-region text:id="ct1"><text:deletion><text:p>SILINMIS_A tablosundan '
      "SILINMIS_B tablosuna yüklenir.</text:p></text:deletion></text:changed-region></text:tracked-changes>"
    "</office:text>"
)


def _ods_row(cells: list[str], repeat: int = 1) -> str:
    rep = f' table:number-rows-repeated="{repeat}"' if repeat > 1 else ""
    return (f"<table:table-row{rep}>{_odf_cells(cells)}"
            '<table:table-cell table:number-columns-repeated="16380"/></table:table-row>')


ODS_BODY = (
    "<office:spreadsheet>"
    '<table:table table:name="STTM">'
    + _ods_row(["Kaynak Tablo", "Hedef Tablo", "Job", "Yükleme Tipi"])
    + _ods_row(["KART_ISLEM_YYYYMMDD.dat", "STG_KART_ISLEM", "TPT_STG_KART", "truncate"])
    + _ods_row([], 3)
    + _ods_row(["STG_KART_ISLEM", "ODS_KART_ISLEM", "SP_ODS_KART", "merge"])
    + _ods_row([], 1048570)
    + "</table:table>"
    '<table:table table:name="Bağımlılık">'
    + _ods_row(["Job", "Önceki İş"])
    + _ods_row(["TPT_STG_KART", "MFT_KART"])
    + _ods_row(["SP_ODS_KART", "TPT_STG_KART"])
    + _ods_row([], 1048573)
    + "</table:table></office:spreadsheet>"
)

VISIO_MASTERS = {1: "Database", 2: "Document", 3: "Process", 4: "Dynamic connector", 5: "Text", 6: "Title"}
VISIO_PAGES = [
    {"name": "Kart akışı",
     "shapes": (_vshape(1, 1, "SRC_KART_ISLEM", 1, 5) + _vshape(2, 2, "KART_ISLEM_YYYYMMDD.dat", 3, 5)
                + _vshape(3, 3, "TPT_STG_KART", 5, 5) + _vshape(4, 1, "STG_KART_ISLEM", 7, 5)
                + _vshape(5, 1, "ODS_KART_ISLEM", 9, 5)
                + _vconnector(10, 4, "EXT_KART") + _vconnector(11, 4) + _vconnector(12, 4)
                # Ters çizilmiş bağlayıcı: ok başı başlangıç ucunda → yön ODS'den STG'ye değil, STG'den ODS'ye.
                + _vconnector(13, 4, "SP_ODS_KART", begin_arrow=13, end_arrow=0)
                + _vshape(20, 5, "Gece zinciri 01:00'de başlar.", 5, 3)
                + _vshape(21, 6, "Kredi Kartı Veri Akışı", 5, 7)),
     "connects": [(10, 1, 2), (11, 2, 3), (12, 3, 4), (13, 5, 4)]},
    {"name": "Arka plan", "background": True,
     "shapes": _vshape(1, 1, "GIZLI_ARKA_PLAN", 1, 1), "connects": []},
    {"name": "Müşteri",
     "shapes": (_vshape(1, 1, "SRC_MUSTERI", 1, 5) + _vshape(2, 1, "STG_MUSTERI", 3, 5)
                + _vshape(3, 1, "ODS_MUSTERI", 5, 5) + _vconnector(4, 4) + _vconnector(5, 4)),
     "connects": [(4, 1, 2), (5, 2, 3)]},
]

DRAWIO_COMPRESSED_MODEL = (
    '<mxGraphModel dx="1000" dy="600" grid="1"><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
    '<mxCell id="s" value="SRC_MUSTERI" style="shape=cylinder3;whiteSpace=wrap;html=1;" vertex="1" parent="1">'
    '<mxGeometry x="20" y="40" width="120" height="60" as="geometry"/></mxCell>'
    '<mxCell id="g" value="STG_MUSTERI" style="shape=cylinder3;html=1;" vertex="1" parent="1">'
    '<mxGeometry x="200" y="40" width="120" height="60" as="geometry"/></mxCell>'
    '<mxCell id="o" value="&lt;b&gt;ODS_MUSTERI&lt;/b&gt;" style="shape=cylinder3;html=1;" vertex="1" parent="1">'
    '<mxGeometry x="380" y="40" width="120" height="60" as="geometry"/></mxCell>'
    '<mxCell id="d" value="DWH.D_MUSTERI&lt;br&gt;&lt;i&gt;SCD2&lt;/i&gt;" style="shape=cylinder3;html=1;" vertex="1" '
    'parent="1"><mxGeometry x="560" y="40" width="120" height="60" as="geometry"/></mxCell>'
    '<mxCell id="e1" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="1" source="s" target="g">'
    '<mxGeometry relative="1" as="geometry"/></mxCell>'
    '<mxCell id="e2" edge="1" parent="1" source="g" target="o"><mxGeometry relative="1" as="geometry"/></mxCell>'
    '<mxCell id="e3" value="LOAD_D_MUSTERI" edge="1" parent="1" source="o" target="d">'
    '<mxGeometry relative="1" as="geometry"/></mxCell>'
    "</root></mxGraphModel>"
)

EML_BODY = (
    "Merhaba,\n\n"
    "Yarından itibaren DWH.MART_KART_OZET tablosundan EXP_BDDK_KART dosyasına EXP_BDDK işiyle\n"
    "aktarım yapılacak. Güncel STTM ve mart yükleme betiği ekte.\n\n"
    "İyi çalışmalar,\nAyşe Yılmaz\nVeri Ambarı Ekibi\n"
)
EML_CSV = b"Kaynak Tablo;Hedef Tablo;Job\nODS_KART_ISLEM;DWH.F_KART_ISLEM;LOAD_F_KART_ISLEM\n"
EML_SQL = ("-- Kart mart yüklemesi\n"
           "INSERT INTO DWH.MART_KART_OZET\n"
           "SELECT m.SEGMENT, f.ISLEM_TARIHI, SUM(f.TUTAR_TL)\n"
           "FROM DWH.F_KART_ISLEM f JOIN DWH.D_MUSTERI m ON m.MUSTERI_KEY = f.MUSTERI_KEY\n"
           "GROUP BY m.SEGMENT, f.ISLEM_TARIHI;\n").encode()
EML_HEADERS = {
    "From": f"{_mime_word('Ayşe Yılmaz')} <ayse.yilmaz@ornek-banka.com.tr>",
    "To": "dwh-ekibi@ornek-banka.com.tr",
    "Subject": "=?utf-8?q?Kart_mart_y=C3=BCklemesi_de=C4=9Fi=C5=9Fikli=C4=9Fi?=",
    "Date": "Tue, 15 Sep 2026 09:15:00 +0300",
    "Message-ID": "<kart-degisiklik-1@ornek-banka.com.tr>",
}

ZIP_SQL = b"-- ODS yukleme\nINSERT INTO ODS_KART_ISLEM SELECT * FROM STG_KART_ISLEM;\n"
ZIP_NOTES = "SRC_KART_ISLEM tablosundan KART_ISLEM_YYYYMMDD.dat dosyasına EXT_KART işiyle extract alınır.\n"
ZIP_JIL = ("insert_job: EXT_KART   job_type: CMD\n\n"
           "insert_job: MFT_KART   job_type: CMD\ncondition: s(EXT_KART)\n\n"
           "insert_job: TPT_STG_KART   job_type: CMD\ncondition: s(MFT_KART)\n")


def kart_paket() -> bytes:
    inner = zip_bytes([("kart_zincir.jil", ZIP_JIL.encode())])
    return zip_bytes([
        ("paket/", b""),
        ("paket/SP_ODS_KART.sql", ZIP_SQL),
        ("paket/notlar.txt", ZIP_NOTES.encode("utf-8")),
        ("paket/ic/zincir.zip", inner),
        ("__MACOSX/paket/._SP_ODS_KART.sql", b"\x00\x05\x16\x07"),
        ("paket/.DS_Store", b"\x00\x00\x00\x01Bud1"),
        ("paket/Thumbs.db", CFB_SIGNATURE),
        ("paket/~$taslak.docx", b"kilit"),
    ])


# --------------------------------------------------------------------------- #
# Zor örnekler
# --------------------------------------------------------------------------- #


def _flag_encrypted(data: bytes) -> bytes:
    """Zip'teki tüm öğelere "şifreli" bayrağını koyar (yerel başlık ve merkez dizin)."""
    out = bytearray(data)
    position = 0
    while (position := out.find(b"PK\x03\x04", position)) >= 0:
        struct.pack_into("<H", out, position + 6, struct.unpack_from("<H", out, position + 6)[0] | 1)
        position += 4
    position = 0
    while (position := out.find(b"PK\x01\x02", position)) >= 0:
        struct.pack_into("<H", out, position + 8, struct.unpack_from("<H", out, position + 8)[0] | 1)
        position += 4
    return bytes(out)


def hard_samples() -> dict[str, bytes]:
    level3 = zip_bytes([("en_derin.zip", zip_bytes([("cok_derin.txt", ZIP_NOTES.encode("utf-8"))]))])
    level2 = zip_bytes([("derin.zip", level3), ("ikinci_duzey.sql", ZIP_SQL)])
    forwarded = ("From: Mehmet Kaya <mehmet.kaya@ornek-banka.com.tr>\r\nSubject: STG notu\r\n"
                 "Date: Mon, 14 Sep 2026 17:00:00 +0300\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
                 "SRC_MUSTERI tablosundan STG_MUSTERI tablosuna aktarılır.\r\n")
    return {
        "bozuk.docx": b"PK\x03\x04" + b"\x14\x00\x00\x00" + b"bu bir docx degil" * 3,
        "yol_asimi.zip": zip_bytes([("notlar.txt", ZIP_NOTES.encode("utf-8")),
                                    ("../../kacak.txt", b"calisma klasorunun disina yazilmamali")]),
        "ic_ice.zip": zip_bytes([("birinci_duzey.txt", ZIP_NOTES.encode("utf-8")), ("ikinci.zip", level2)]),
        "bomba_taklidi.zip": zip_bytes([("sifirlar.txt", b"\0" * (5 * 1024 * 1024))], zipfile.ZIP_DEFLATED),
        "sifreli.zip": _flag_encrypted(zip_bytes([("gizli.txt", b"sifreli olmasi gereken icerik")])),
        "ekli_eposta.eml": eml_bytes(
            {"From": f"{_mime_word('Ayşe Yılmaz')} <ayse.yilmaz@ornek-banka.com.tr>", "Subject": "Ekler",
             "Date": "Tue, 15 Sep 2026 10:00:00 +0300"},
            "Ekteki STTM'ye bakınız. Ayrıca iletilen notu okuyun.\n",
            [("kart_sttm_ek.csv", "text/csv", EML_CSV),
             ("stg_notu.eml", "message/rfc822", forwarded.encode("utf-8")),
             ("imza.png", "image/png", _PNG_1X1)],
            boundary="=_dwhflow_zor_0"),
    }


def generate(formats: Path = FORMATS, hard: Path = HARD) -> list[Path]:
    formats.mkdir(parents=True, exist_ok=True)
    hard.mkdir(parents=True, exist_ok=True)
    drawio = ('<?xml version="1.0" encoding="UTF-8"?>\n<mxfile host="app.diagrams.net" version="24.7.0">'
              f'<diagram id="musteri" name="Müşteri hattı">{drawio_compress(DRAWIO_COMPRESSED_MODEL)}</diagram>'
              "</mxfile>\n")
    files = {
        formats / "kart_sttm_eski.xls": xls_bytes(XLS_SHEETS),
        formats / "kart_tasarim_eski.doc": doc_bytes(DOC_PARAGRAPHS),
        formats / "kart_mimari_sunum.pptx": pptx_bytes(PPTX_SLIDES),
        formats / "kart_tasarim.odt": odf_bytes("application/vnd.oasis.opendocument.text", ODT_BODY),
        formats / "kart_sttm.ods": odf_bytes("application/vnd.oasis.opendocument.spreadsheet", ODS_BODY),
        formats / "kart_akis.vsdx": vsdx_bytes(VISIO_PAGES, VISIO_MASTERS),
        formats / "kart_mimari_drawio.xml": drawio.encode("utf-8"),
        formats / "kart_degisiklik.eml": eml_bytes(EML_HEADERS, EML_BODY, [
            ("kart_sttm_ek.csv", "text/csv", EML_CSV), ("LOAD_MART_KART_OZET.sql", "application/sql", EML_SQL),
            ("imza.png", "image/png", _PNG_1X1)]),
        formats / "kart_paket.zip": kart_paket(),
    }
    files.update({hard / name: data for name, data in hard_samples().items()})
    for path, data in files.items():
        path.write_bytes(data)
    return list(files)


if __name__ == "__main__":
    targets = (Path(sys.argv[1]), Path(sys.argv[2])) if len(sys.argv) > 2 else (FORMATS, HARD)
    for written in generate(*targets):
        print(f"yazıldı: {written}")

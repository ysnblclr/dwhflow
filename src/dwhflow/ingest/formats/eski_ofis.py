"""Eski Office biçimleri: ``.xls`` (Excel 97–2003) ve ``.doc`` (Word 97–2003).

**.xls** — ``xlrd`` ile; Excel okuyucusuyla aynı anlam: her sayfa tablo olarak yorumlanır
(konum = sayfa adı, satırlar ``satır n``), tablo tanınmazsa uzun metin hücreleri metin
kurallarından geçer. Uzantısı ``.xls`` olup içeriği başka olan dosyalar da okunur:
aslında ``.xlsx`` (zip), HTML tablosu (web dışa aktarımları), "XML Elektronik Tablo 2003"
ve ayırıcılı metin (CSV/TSV).

**.doc** — LibreOffice (``soffice``) bulunursa belge geçici klasörde ``.docx``'e
dönüştürülür (en çok ``CONVERT_TIMEOUT`` sn) ve Word okuyucusuyla okunur; konumlar Word ile
aynıdır. Bulunamazsa (ya da dönüştürme başarısızsa) ikili dosyadan metin çıkarılır: Word 97+
parça tablosu (``xlrd.compdoc`` ile OLE kabı), olmazsa UTF-16LE/cp1254 yazdırılabilir diziler.
Bu yolda biçim ve tablolar kaybolur (tablo satırları "A | B" metni olur); raporda not düşülür
ve ``.docx`` önerilir. ``.doc`` uzantılı RTF, MHTML (Confluence "Word'e aktar") ve HTML
dosyaları kendi ayrıştırıcılarıyla okunur. Makrolar hiçbir koşulda çalıştırılmaz.

LibreOffice yolu ``DWHFLOW_LIBREOFFICE`` ortam değişkeniyle verilebilir; ``0``/``yok`` kapatır.
"""

from __future__ import annotations

import csv
import glob
import io
import os
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..base import DocReport, ModelBuilder
from ..preview import preview_docx
from ..readers import MAX_ROWS_PER_SHEET, _decode, read_docx
from ..registry import Block, FormatSpec, register
from ._html import decode_html, html_items, looks_like_html, looks_like_mime, mime_html
from ._ortak import (
    MAX_BINARY_FILE_BYTES,
    FormatError,
    Item,
    Leftovers,
    Sheet,
    cap_rows,
    fail,
    looks_like_heading,
    parse_xml,
    preview_items,
    preview_sheets,
    read_items,
    read_limited,
    read_member,
    read_sheets,
    squeeze,
    strip_namespaces,
)
from ._rtf import looks_like_rtf, rtf_items

OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
CONVERT_TIMEOUT = 120
_BIFF_BOF = (b"\x09\x00", b"\x09\x02", b"\x09\x04", b"\x09\x08")
_MAX_COLUMNS = 1024

# --------------------------------------------------------------------------- #
# .xls
# --------------------------------------------------------------------------- #


def _xls_value(cell: Any, datemode: int) -> Any:
    import xlrd

    kind, value = cell.ctype, cell.value
    if kind in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return None
    if kind == xlrd.XL_CELL_NUMBER:
        return int(value) if float(value).is_integer() and abs(value) < 1e15 else value
    if kind == xlrd.XL_CELL_DATE:
        try:
            return xlrd.xldate.xldate_as_datetime(value, datemode)
        except (ValueError, OverflowError, xlrd.xldate.XLDateError):
            return value
    if kind == xlrd.XL_CELL_BOOLEAN:
        return bool(value)
    if kind == xlrd.XL_CELL_ERROR:
        return xlrd.error_text_from_code.get(value, "#HATA")
    return value


def _biff_sheets(data: bytes, report: DocReport | None) -> list[Sheet]:
    import xlrd

    try:
        book = xlrd.open_workbook(file_contents=data, logfile=io.StringIO(), on_demand=True)
    except Exception as exc:  # noqa: BLE001 - xlrd birçok türde hata fırlatır
        if "encrypt" in str(exc).lower():
            raise FormatError("Excel dosyası şifreli; şifresini kaldırıp yükleyin") from exc
        raise FormatError(f"Excel (.xls) açılamadı: {exc}") from exc
    sheets: list[Sheet] = []
    try:
        for index in range(book.nsheets):
            sheet = book.sheet_by_index(index)
            rows = [[_xls_value(cell, book.datemode) for cell in sheet.row(r)[:_MAX_COLUMNS]]
                    for r in range(min(sheet.nrows, MAX_ROWS_PER_SHEET + 1))]
            sheets.append((sheet.name, cap_rows(sheet.name, rows, report)))
            book.unload_sheet(index)
    finally:
        book.release_resources()
    return sheets


def _xlsx_sheets(data: bytes, report: DocReport | None) -> list[Sheet]:
    import openpyxl

    try:
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise FormatError(f"dosya .xlsx gibi görünüyor ama açılamadı: {exc}") from exc
    sheets: list[Sheet] = []
    try:
        for sheet in book.worksheets:
            rows = []
            for index, row in enumerate(sheet.iter_rows(values_only=True)):
                if index > MAX_ROWS_PER_SHEET:
                    break
                rows.append(list(row))
            sheets.append((sheet.title, cap_rows(sheet.title, rows, report)))
    finally:
        book.close()
    return sheets


def _spreadsheetml_sheets(data: bytes, report: DocReport | None) -> list[Sheet]:
    """"XML Elektronik Tablo 2003" (``<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet">``)."""
    root = strip_namespaces(parse_xml(data))
    sheets: list[Sheet] = []
    for number, worksheet in enumerate(root.iter("Worksheet"), start=1):
        title = worksheet.get("Name") or f"Sayfa{number}"
        rows: list[list[Any]] = []
        table = worksheet.find("Table")
        for row in table.iter("Row") if table is not None else []:
            index = _index(row.get("Index"))
            while index and len(rows) < min(index - 1, MAX_ROWS_PER_SHEET + 1):
                rows.append([])
            cells: list[Any] = []
            for cell in row.findall("Cell"):
                column = _index(cell.get("Index"))
                while column and len(cells) < min(column - 1, _MAX_COLUMNS):
                    cells.append(None)
                value = cell.find("Data")
                cells.append("".join(value.itertext()) if value is not None else None)
                cells.extend([None] * min(_index(cell.get("MergeAcross")) or 0, _MAX_COLUMNS))
            rows.append(cells[:_MAX_COLUMNS])
            if len(rows) > MAX_ROWS_PER_SHEET:
                break
        sheets.append((title, cap_rows(title, rows, report)))
    if not sheets:
        raise FormatError("XML dosyasında çalışma sayfası (Worksheet) yok")
    return sheets


def _index(value: str | None) -> int:
    try:
        return max(0, int(str(value)))
    except (TypeError, ValueError):
        return 0


def _text_sheets(data: bytes, title: str) -> list[Sheet]:
    text = _decode(data)
    sample = text[:4096]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = "\t" if sample.count("\t") >= max(sample.count(";"), sample.count(",")) else (
            ";" if sample.count(";") > sample.count(",") else ",")
    rows: list[list[Any]] = list(csv.reader(io.StringIO(text), delimiter=delimiter))[:MAX_ROWS_PER_SHEET]
    return [(title, rows)]


def xls_sheets(path: Path, report: DocReport | None = None) -> list[Sheet]:
    data = read_limited(path, MAX_BINARY_FILE_BYTES)
    if data.startswith(OLE_SIGNATURE) or data[:2] in _BIFF_BOF:
        return _biff_sheets(data, report)
    if data.startswith(b"PK\x03\x04"):
        if report is not None:
            report.note("dosya aslında .xlsx biçiminde")
        return _xlsx_sheets(data, report)
    if looks_like_html(data):
        if report is not None:
            report.note("dosya aslında HTML tablosu (web dışa aktarımı)")
        tables = [item.rows for item in html_items(decode_html(data)) if item.kind == "table"]
        return [(f"tablo {number}", rows) for number, rows in enumerate(tables, start=1)]
    head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    if head.startswith(b"<") and b"urn:schemas-microsoft-com:office:spreadsheet" in data[:65536]:
        if report is not None:
            report.note("dosya aslında XML Elektronik Tablo 2003 biçiminde")
        return _spreadsheetml_sheets(data, report)
    if b"\x00" in data[:4096]:
        raise FormatError("Excel dosyası tanınmadı (xls, xlsx, HTML ya da ayırıcılı metin değil)")
    if report is not None:
        report.note("dosya aslında ayırıcılı metin (CSV/TSV)")
    return _text_sheets(data, path.stem)


def read_xls(builder: ModelBuilder, report: DocReport, path: Path) -> Leftovers:
    try:
        sheets = xls_sheets(path, report)
    except FormatError as exc:
        return fail(report, str(exc))
    if not sheets:
        report.note("dosyada tablo bulunamadı")
        return []
    return read_sheets(builder, report, path.name, sheets)


def preview_xls(path: Path) -> list[Block]:
    return preview_sheets(xls_sheets(path))


# --------------------------------------------------------------------------- #
# .doc — ikili Word (97–2003)
# --------------------------------------------------------------------------- #

_FALLBACK_NOTE = ("LibreOffice bulunamadı: .doc içinden yalnız düz metin çıkarıldı; biçim ve tablolar "
                  "kaybolmuş olabilir. En iyi sonuç için belgeyi Word'de .docx olarak kaydedip yükleyin.")
_WORD_CHARS = {0x07: "\x07", 0x09: " ", 0x0B: "\n", 0x0C: "\r", 0x0E: "\r", 0x1E: "-", 0x1F: "", 0xA0: " ",
               0x01: "", 0x02: "", 0x03: "", 0x04: "", 0x05: "", 0x08: ""}


@dataclass
class _WordBinary:
    encrypted: bool = False
    text: str | None = None  # parça tablosundan metin; None → çıkarılamadı
    stream: bytes = b""  # WordDocument akışı (ham metin taraması için)


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _piece_text(word: bytes, table: bytes, fc_clx: int, lcb_clx: int, ccp_text: int) -> str:
    """Word 97+ parça tablosu (CLX → PlcPcd) ile ana metin."""
    clx = table[fc_clx:fc_clx + lcb_clx]
    position = 0
    while position < len(clx) and clx[position] == 0x01:  # Prc: biçim özellikleri, atla
        position += 3 + max(struct.unpack_from("<h", clx, position + 1)[0], 0)
    if position >= len(clx) or clx[position] != 0x02:
        raise FormatError("parça tablosu bulunamadı")
    size = _u32(clx, position + 1)
    plc = clx[position + 5:position + 5 + size]
    count = (len(plc) - 4) // 12
    if count <= 0:
        raise FormatError("parça tablosu boş")
    positions = struct.unpack_from(f"<{count + 1}I", plc, 0)
    parts: list[str] = []
    total = 0
    for index in range(count):
        start, end = positions[index], positions[index + 1]
        if end <= start:
            continue
        fc = _u32(plc, 4 * (count + 1) + 8 * index + 2)
        length = end - start
        if fc & 0x40000000:  # sıkıştırılmış: 8 bit (cp1252)
            offset = (fc & 0x3FFFFFFF) // 2
            parts.append(word[offset:offset + length].decode("cp1252", errors="replace"))
        else:
            offset = fc & 0x3FFFFFFF
            parts.append(word[offset:offset + 2 * length].decode("utf-16-le", errors="replace"))
        total += length
        if ccp_text and total >= ccp_text:
            break
    text = "".join(parts)
    return text[:ccp_text] if ccp_text > 0 else text


def _word_binary(data: bytes) -> _WordBinary:
    from xlrd.compdoc import CompDoc

    result = _WordBinary()
    try:
        document = CompDoc(data, logfile=io.StringIO())
        word = document.get_named_stream("WordDocument")
    except Exception:  # noqa: BLE001 - bozuk OLE kabı: ham taramaya düşülür
        return result
    if not word or len(word) < 0x22 or _u16(word, 0) != 0xA5EC:
        return result
    result.stream = word
    try:
        nfib, flags = _u16(word, 2), _u16(word, 0x0A)
        result.encrypted = bool(flags & 0x0100)
        if result.encrypted:
            return result
        if nfib < 0xC1:  # Word 6/95: metin fcMin..fcMac arasında, 8 bit
            fc_min, fc_mac = _u32(word, 0x18), _u32(word, 0x1C)
            result.text = word[fc_min:fc_mac].decode("cp1254", errors="replace")
            return result
        csw = _u16(word, 0x20)
        position = 0x22 + 2 * csw
        cslw = _u16(word, position)
        ccp_text = struct.unpack_from("<i", word, position + 2 + 12)[0]
        position += 2 + 4 * cslw
        pairs = _u16(word, position)
        if pairs < 34:
            return result
        fc_clx, lcb_clx = struct.unpack_from("<II", word, position + 2 + 33 * 8)
        table = document.get_named_stream("1Table" if flags & 0x0200 else "0Table")
        if table:
            result.text = _piece_text(word, table, fc_clx, lcb_clx, max(ccp_text, 0))
    except Exception:  # noqa: BLE001 - FIB/CLX bozuksa ham taramaya düşülür
        result.text = None
    return result


def _strip_fields(text: str) -> str:
    """Alan kodlarını (``\\x13 KOD \\x14 sonuç \\x15``) atar, sonucu bırakır."""
    out: list[str] = []
    stack: list[bool] = []  # True: alanın sonuç bölümündeyiz
    for char in text:
        if char == "\x13":
            stack.append(False)
        elif char == "\x14":
            if stack:
                stack[-1] = True
        elif char == "\x15":
            if stack:
                stack.pop()
        elif all(stack):
            out.append(char)
    return "".join(out)


_RUN = re.compile(r"[A-Za-z0-9ÇĞİÖŞÜçğıöşüÂâÎîÛû _.,;:!?'\"()\-/%&+=*#@$<>\[\]\r\n\t]{8,}")


def _printable_runs(data: bytes) -> str:
    """Son çare: UTF-16LE ve cp1254 çözümlerinden okunabilir diziler."""
    runs: list[str] = []
    for text in (data.decode("utf-16-le", errors="replace"), data[1:].decode("utf-16-le", errors="replace"),
                 data.decode("cp1254", errors="replace")):
        runs.extend(run for run in _RUN.findall(text) if re.search(r"[A-Za-zÇĞİÖŞÜçğıöşü]{3}", run))
    return "\r".join(dict.fromkeys(run.strip() for run in runs if run.strip()))


def word_text_items(text: str) -> list[Item]:
    """Word ikili metni → paragraflar (tablo satırları "A | B" metni olur)."""
    text = _strip_fields(text).translate(_WORD_CHARS).replace("\x07\x07", "\x07\r")
    items: list[Item] = []
    for raw in text.split("\r"):
        if "\x07" in raw:
            raw = " | ".join(cell.strip() for cell in raw.split("\x07")).strip(" |")
        paragraph = squeeze(raw)
        if paragraph:
            items.append(Item("heading" if looks_like_heading(paragraph) else "paragraph", paragraph))
    return items


def find_soffice() -> str | None:
    configured = os.environ.get("DWHFLOW_LIBREOFFICE", "").strip()
    if configured.lower() in ("0", "yok", "hayir", "hayır", "false", "no", "off"):
        return None
    if configured:
        return configured if Path(configured).is_file() else shutil.which(configured)
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    candidates = [r"C:\Program Files\LibreOffice\program\soffice.exe",
                  r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
                  "/Applications/LibreOffice.app/Contents/MacOS/soffice",
                  "/usr/lib/libreoffice/program/soffice", *sorted(glob.glob("/opt/libreoffice*/program/soffice"))]
    return next((c for c in candidates if Path(c).is_file()), None)


def _kill_tree(process: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, timeout=30,
                           check=False)
        else:
            import signal

            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        process.kill()
        process.wait(timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def run_command(command: list[str], timeout: int) -> int:
    """Kabuk kullanmadan çalıştırır; süre aşılırsa süreç ağacını sonlandırır."""
    options: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                               "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(command, **options)
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_tree(process)
        raise FormatError(f"LibreOffice {timeout} sn içinde bitmedi") from exc


def convert_with_libreoffice(soffice: str, source: Path, work: Path) -> Path:
    """``source``'u ``work`` içinde ``.docx``'e çevirir; dönüşen dosya özgün adı taşır."""
    staged = work / "girdi.doc"
    shutil.copyfile(source, staged)
    out = work / "cikti"
    out.mkdir()
    profile = (work / "profil").resolve().as_uri()
    code = run_command([soffice, "--headless", "--norestore", "--nologo", "--nodefault", "--nolockcheck",
                        f"-env:UserInstallation={profile}", "--convert-to", "docx:MS Word 2007 XML",
                        "--outdir", str(out), str(staged)], CONVERT_TIMEOUT)
    converted = out / "girdi.docx"
    if not converted.is_file():
        raise FormatError(f"dönüştürme çıktısı yok (çıkış kodu {code})")
    with zipfile.ZipFile(converted) as archive:
        if b"<w:t" not in read_member(archive, "word/document.xml"):
            raise FormatError("dönüştürülen belgede metin yok")
    named = work / "belge"
    named.mkdir()
    # Word okuyucusu kaynak olarak dosya adını yazar: özgün ad korunsun.
    return Path(shutil.move(str(converted), str(named / source.name)))


@dataclass
class _DocSource:
    docx: Path | None = None  # Word okuyucusuyla okunacak (dönüştürülmüş ya da aslında .docx)
    items: list[Item] = field(default_factory=list)
    table_reader: str = "doc_tablo"
    notes: list[str] = field(default_factory=list)


@contextmanager
def _doc_source(path: Path) -> Iterator[_DocSource]:
    data = read_limited(path, MAX_BINARY_FILE_BYTES)
    if data.startswith(b"PK\x03\x04"):
        yield _DocSource(docx=path, notes=["dosya aslında .docx biçiminde; Word okuyucusuyla okundu"])
        return
    if looks_like_rtf(data):
        yield _DocSource(items=rtf_items(data), table_reader="rtf_tablo",
                         notes=["dosya aslında RTF biçiminde"])
        return
    if looks_like_mime(data):
        markup = mime_html(data)
        if markup is None:
            raise FormatError("MHTML dosyasında HTML bölümü yok")
        yield _DocSource(items=html_items(markup), table_reader="html_tablo",
                         notes=["dosya MHTML (web arşivi; ör. Confluence 'Word'e aktar') biçiminde"])
        return
    if looks_like_html(data):
        yield _DocSource(items=html_items(decode_html(data)), table_reader="html_tablo",
                         notes=["dosya aslında HTML biçiminde"])
        return
    if not data.startswith(OLE_SIGNATURE):
        raise FormatError("Word belgesi tanınmadı (.doc, .docx, RTF ya da HTML değil)")
    binary = _word_binary(data)
    if binary.encrypted:
        raise FormatError("Word belgesi şifreli; şifresini kaldırıp yükleyin")
    notes: list[str] = []
    soffice = find_soffice()
    if soffice:
        with tempfile.TemporaryDirectory(prefix="dwhflow-doc-") as work:
            try:
                converted = convert_with_libreoffice(soffice, path, Path(work))
            except (FormatError, OSError, zipfile.BadZipFile) as exc:
                notes.append(f"LibreOffice ile dönüştürülemedi ({exc}); düz metin çıkarımına geçildi")
            else:
                yield _DocSource(docx=converted, notes=["LibreOffice ile .docx'e dönüştürülerek okundu"])
                return
    text = binary.text if binary.text is not None else _printable_runs(binary.stream or data)
    notes.append(_FALLBACK_NOTE if soffice is None else
                 "Biçim ve tablolar kaybolmuş olabilir; en iyi sonuç için .docx olarak kaydedip yükleyin.")
    yield _DocSource(items=word_text_items(text), notes=notes)


def read_doc(builder: ModelBuilder, report: DocReport, path: Path) -> Leftovers:
    try:
        with _doc_source(path) as source:
            for note in source.notes:
                report.note(note)
            if source.docx is not None:
                return read_docx(builder, report, source.docx)
            if not source.items:
                report.note("belgede metin bulunamadı")
                return []
            return read_items(builder, report, path.name, source.items, table_reader=source.table_reader)
    except FormatError as exc:
        return fail(report, str(exc))


def preview_doc(path: Path) -> list[Block]:
    with _doc_source(path) as source:
        notes: list[Block] = [{"locator": "", "kind": "note", "text": note} for note in source.notes]
        if source.docx is not None:
            return notes + preview_docx(source.docx)
        return notes + preview_items(source.items)


register(FormatSpec("xls", "Excel 97-2003", (".xls",), read_xls, preview_xls, group="Tablo",
                    description="Eski Excel (xlrd); STTM/eşleme ve job tabloları, Excel ile aynı anlam",
                    sample="ornek_bicimler/kart_sttm_eski.xls"))
register(FormatSpec("doc", "Word 97-2003", (".doc",), read_doc, preview_doc,
                    description="LibreOffice varsa .docx'e dönüştürülür; yoksa düz metin çıkarılır",
                    sample="ornek_bicimler/kart_tasarim_eski.doc"))

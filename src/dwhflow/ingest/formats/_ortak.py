"""Biçim okuyucularının ortak yardımcıları (``_`` ile başladığı için eklenti olarak yüklenmez).

* Güvenilmeyen XML yalnız ``defusedxml`` ile ayrıştırılır; zip üyeleri boyut sınırıyla okunur.
* Doküman benzeri biçimler (eski Word, RTF, ODT, HTML) içeriği ``Item`` listesine çevirir;
  konum etiketleri **tek yerde** (``place``) üretilir, böylece okuyucu ile önizleme aynı
  etiketleri kullanır. Etiketler Word okuyucusuyla aynıdır: ``§ Başlık · paragraf 3``,
  ``tablo 2 (§ Başlık)``; tablo satırları ``… · satır n``.
* Hesap tablosu biçimleri (``.xls``, ``.ods``) Excel okuyucusunun anlamını birebir izler.
"""

from __future__ import annotations

import html
import importlib.util
import posixpath
import re
import sys
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml import ElementTree as SafeET

from ..base import DocReport, ModelBuilder
from ..preview import MAX_BLOCKS, MAX_PREVIEW_ROWS, _table_block
from ..readers import MAX_ROWS_PER_SHEET, MAX_XML_BYTES
from ..registry import Block
from ..tables import interpret, rows_to_view
from ..text import extract_paragraphs, split_markdown

Leftovers = list[tuple[str, str]]

#: Metin tabanlı biçimlerde (HTML, RTF, Mermaid, DOT, şablon JSON) okunacak en büyük dosya.
MAX_TEXT_FILE_BYTES = 40 * 1024 * 1024
#: İkili ofis dosyalarında (xls, doc) okunacak en büyük dosya.
MAX_BINARY_FILE_BYTES = 100 * 1024 * 1024


class FormatError(ValueError):
    """Kullanıcı dosyası okunamadı; ileti olduğu gibi rapora yazılır."""


def module_available(name: str) -> bool:
    """İsteğe bağlı paket kurulu mu? (``sys.modules``'a konmuş sahte modüller de sayılır.)"""
    if sys.modules.get(name) is not None:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def fail(report: DocReport, message: str) -> Leftovers:
    """Okuyucudan hata ile çıkış: durum ``error``, not rapora."""
    report.status = "error"
    report.note(message)
    return []


def read_limited(path: Path, limit: int) -> bytes:
    """Dosyayı en çok ``limit`` bayt okur; büyükse ``FormatError``."""
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise FormatError(f"dosya çok büyük (sınır {limit // 2**20} MB)")
    return data


# --------------------------------------------------------------------------- #
# XML ve zip
# --------------------------------------------------------------------------- #


def parse_xml(data: bytes | str) -> Element:
    """Güvenilmeyen XML'i ayrıştırır: DTD varlıkları ve dış başvurular yasaktır (XXE yok)."""
    try:
        return SafeET.fromstring(data)
    except DefusedXmlException as exc:
        raise FormatError(f"güvensiz XML reddedildi ({type(exc).__name__})") from exc
    except ParseError as exc:
        raise FormatError(f"XML ayrıştırılamadı: {exc}") from exc


def local(tag: Any) -> str:
    """``{ad alanı}etiket`` → ``etiket``."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def strip_namespaces(root: Element) -> Element:
    """Etiket ve öznitelik adlarındaki ad alanlarını atar (kullanıcı yolları sade yazılsın)."""
    for element in root.iter():
        if isinstance(element.tag, str):
            element.tag = local(element.tag)
        if any("}" in key for key in element.attrib):
            element.attrib = {local(key): value for key, value in element.attrib.items()}
    return root


def open_zip(path: Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise FormatError(f"zip paketi açılamadı (bozuk ya da farklı biçim): {exc}") from exc


def read_member(archive: zipfile.ZipFile, name: str, limit: int = MAX_XML_BYTES) -> bytes:
    """Zip üyesini boyut sınırıyla okur (merkez dizindeki boyut yalan söyleyebilir)."""
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise FormatError(f"pakette '{name}' yok") from exc
    if info.flag_bits & 0x1:
        raise FormatError("paket şifreli")
    if info.file_size > limit:
        raise FormatError(f"'{name}' çok büyük (sınır {limit // 2**20} MB)")
    try:
        with archive.open(info) as handle:
            data = handle.read(limit + 1)
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError, EOFError) as exc:
        raise FormatError(f"'{name}' okunamadı: {exc}") from exc
    if len(data) > limit:
        raise FormatError(f"'{name}' çok büyük (sınır {limit // 2**20} MB)")
    return data


def member_xml(archive: zipfile.ZipFile, name: str) -> Element:
    return parse_xml(read_member(archive, name))


def zip_has(archive: zipfile.ZipFile, name: str) -> bool:
    try:
        archive.getinfo(name)
    except KeyError:
        return False
    return True


_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def zip_rels(archive: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    """OPC ilişkileri (pptx, vsdx): kimlik → (tür, hedef parça yolu). Dış bağlantılar atlanır."""
    folder, name = posixpath.split(part)
    rels_name = posixpath.join(folder, "_rels", f"{name}.rels")
    if not zip_has(archive, rels_name):
        return {}
    out: dict[str, tuple[str, str]] = {}
    for rel in member_xml(archive, rels_name).iter(f"{_REL}Relationship"):
        target = rel.get("Target") or ""
        if rel.get("TargetMode") == "External" or not target:
            continue
        resolved = target.lstrip("/") if target.startswith("/") else posixpath.normpath(
            posixpath.join(folder, target))
        out[rel.get("Id") or ""] = ((rel.get("Type") or "").rsplit("/", 1)[-1], resolved)
    return out


# --------------------------------------------------------------------------- #
# Metin
# --------------------------------------------------------------------------- #

_INLINE_SPACE = re.compile(r"[ \t\r\f\v   -​  　﻿]+")


def squeeze(text: str) -> str:
    """Satır içi boşlukları teke indirir; satır sonlarını korur, boş satırları atar."""
    lines = (_INLINE_SPACE.sub(" ", line).strip() for line in str(text).split("\n"))
    return "\n".join(line for line in lines if line)


def one_line(text: str) -> str:
    return " ".join(str(text).split())


_HTML_BREAK = re.compile(r"<\s*(?:br|p|div|li|h[1-6]|tr)\b[^>]*>|<\s*/\s*(?:p|div|li|h[1-6]|tr)\s*>", re.IGNORECASE)
_HTML_TAG = re.compile(r"<[^>]*>")


def html_to_text(value: str) -> str:
    """Kısa HTML parçası (diyagram etiketi) → düz metin; blok etiketleri satır sonu olur."""
    return squeeze(html.unescape(_HTML_TAG.sub("", _HTML_BREAK.sub("\n", str(value)))))


_NUMBERED = re.compile(r"^\d+(\.\d+)*\.?\s+\S")


def looks_like_heading(text: str) -> bool:
    """Biçim bilgisi olmayan metinde numaralı başlık (``2.1 Yükleme``).

    Word okuyucusundaki kurala ek olarak nokta/iki nokta ile biten satırlar başlık sayılmaz:
    ``1 STG_A tablosundan ODS_A tablosuna yüklenir.`` bir maddedir, başlık değil.
    """
    stripped = text.strip()
    return (bool(_NUMBERED.match(stripped)) and len(stripped) < 90 and "\n" not in stripped
            and not stripped.endswith((".", ":", ";", ",")))


# --------------------------------------------------------------------------- #
# Doküman öğeleri: başlık / paragraf / tablo
# --------------------------------------------------------------------------- #


@dataclass
class Item:
    kind: str  # heading | paragraph | table
    text: str = ""
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class Placed:
    item: Item
    locator: str


def place(items: Iterable[Item]) -> list[Placed]:
    """Öğelere konum etiketi verir (Word okuyucusuyla aynı şema)."""
    heading, paragraphs, tables = "", 0, 0
    out: list[Placed] = []
    for item in items:
        if item.kind == "heading":
            heading = one_line(item.text)[:60]
            if heading:
                out.append(Placed(item, f"§ {heading}"))
        elif item.kind == "paragraph":
            paragraphs += 1
            out.append(Placed(item, f"§ {heading} · paragraf {paragraphs}" if heading
                              else f"paragraf {paragraphs}"))
        elif item.kind == "table":
            tables += 1
            out.append(Placed(item, f"tablo {tables}" + (f" (§ {heading})" if heading else "")))
    return out


def read_items(builder: ModelBuilder, report: DocReport, file: str, items: Iterable[Item], *,
               table_reader: str) -> Leftovers:
    """Tabloları tablo yorumlayıcısından, paragrafları metin kurallarından geçirir."""
    paragraphs: list[tuple[str, str]] = []
    for placed in place(items):
        item = placed.item
        if item.kind == "table":
            view = rows_to_view(item.rows, locator=placed.locator, row_locator="satır {n}")
            if view:
                report.flows += interpret(view, builder, report, file=file, reader=table_reader)
        elif item.kind == "paragraph" and item.text.strip():
            paragraphs.append((placed.locator, item.text))
    result = extract_paragraphs(paragraphs, builder, report, file=file)
    report.flows += result.flows
    return result.leftovers


def text_blocks(text: str) -> list[Block]:
    """Metin/Markdown önizlemesi (``preview.preview_markdown`` ile aynı konumlar, dosya yerine metin)."""
    tables, paragraphs = split_markdown(text)
    blocks: list[Block] = [{"locator": where, "kind": "paragraph", "text": body} for where, body in paragraphs]
    for table in tables:
        blocks.append({"locator": table.locator, "kind": "table", "headers": table.headers,
                       "rows": table.rows[:MAX_PREVIEW_ROWS], "row_locator": table.row_locator})
    return blocks[:MAX_BLOCKS]


def preview_items(items: Iterable[Item]) -> list[Block]:
    blocks: list[Block] = []
    for placed in place(items):
        item = placed.item
        if item.kind == "table":
            block = _table_block(item.rows, placed.locator)
            if block:
                blocks.append(block)
        elif item.text.strip():
            blocks.append({"locator": placed.locator,
                           "kind": "heading" if item.kind == "heading" else "paragraph",
                           "text": item.text})
        if len(blocks) >= MAX_BLOCKS:
            break
    return blocks


# --------------------------------------------------------------------------- #
# Hesap tabloları (Excel okuyucusunun anlamı)
# --------------------------------------------------------------------------- #

Sheet = tuple[str, list[list[Any]]]


def read_sheets(builder: ModelBuilder, report: DocReport, file: str, sheets: Iterable[Sheet], *,
                table_reader: str = "excel_sttm") -> Leftovers:
    """Her sayfa tablo olarak yorumlanır; tanınmayan sayfadaki uzun metinler kurallardan geçer."""
    leftovers: Leftovers = []
    for title, rows in sheets:
        view = rows_to_view(rows, locator=title, row_locator="satır {n}")
        found = interpret(view, builder, report, file=file, reader=table_reader) if view else 0
        report.flows += found
        if not found:
            texts = [(f"{title} · satır {i + 1}", str(c)) for i, row in enumerate(rows)
                     for c in row if isinstance(c, str) and len(c) >= 30]
            result = extract_paragraphs(texts, builder, report, file=file)
            report.flows += result.flows
            leftovers.extend(result.leftovers)
    return leftovers


def preview_sheets(sheets: Iterable[Sheet]) -> list[Block]:
    blocks: list[Block] = []
    for title, rows in sheets:
        block = _table_block([["" if c is None else str(c) for c in row] for row in rows], title)
        blocks.append(block or {"locator": title, "kind": "note", "text": "Sayfada tablo bulunamadı."})
        if len(blocks) >= MAX_BLOCKS:
            break
    return blocks


def cap_rows(title: str, rows: list[list[Any]], report: DocReport | None) -> list[list[Any]]:
    if len(rows) > MAX_ROWS_PER_SHEET:
        if report is not None:
            report.note(f"{title}: ilk {MAX_ROWS_PER_SHEET} satır okundu")
        return rows[:MAX_ROWS_PER_SHEET]
    return rows

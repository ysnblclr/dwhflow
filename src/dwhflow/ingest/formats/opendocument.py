"""OpenDocument: ``.odt`` (metin) ve ``.ods`` (hesap tablosu) — ``content.xml`` (zip + XML).

**.odt** — başlıklar (``text:h``) konum olur; paragraflar ve liste maddeleri metin
kurallarına, tablolar tablo yorumlayıcısına gider. Konumlar Word ile aynı şemadadır
(``§ Başlık · paragraf n``, ``tablo n (§ Başlık)``). Dipnotlar, yorumlar, izlenen
değişikliklerdeki silinmiş metin ve içindekiler tablosu okunmaz.

**.ods** — her sayfa Excel okuyucusunun anlamıyla okunur (konum = sayfa adı). Tekrarlanan
satır/sütunlar (``number-*-repeated``) sınırla açılır; sondaki boş satırlar üretilmez.
Şifreli belgeler raporda belirtilir.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from pathlib import Path
from xml.etree.ElementTree import Element

from ..base import DocReport, ModelBuilder
from ..readers import MAX_ROWS_PER_SHEET
from ..registry import Block, FormatSpec, register
from ._ortak import (
    FormatError,
    Item,
    Leftovers,
    Sheet,
    cap_rows,
    fail,
    local,
    member_xml,
    open_zip,
    preview_items,
    preview_sheets,
    read_items,
    read_sheets,
    squeeze,
)

_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_MAX_COLUMNS = 1024
_SKIP_INLINE = {f"{_TEXT}note", f"{_OFFICE}annotation", f"{_OFFICE}annotation-end", f"{_TEXT}ruby-text"}
_CONTAINERS = {f"{_TEXT}section", f"{_TEXT}list", f"{_TEXT}list-item", f"{_TEXT}list-header",
               f"{_TEXT}soft-page-break"}


def _content(path: Path) -> Element:
    with open_zip(path) as archive:
        if _encrypted(archive):
            raise FormatError("belge parola ile şifrelenmiş; şifresiz kaydedip yükleyin")
        return member_xml(archive, "content.xml")


def _encrypted(archive: zipfile.ZipFile) -> bool:
    try:
        manifest = member_xml(archive, "META-INF/manifest.xml")
    except FormatError:
        return False
    return any(local(element.tag) == "encryption-data" for element in manifest.iter())


def _count(value: str | None) -> int:
    try:
        return max(1, int(str(value)))
    except (TypeError, ValueError):
        return 1


def inline_text(element: Element) -> str:
    """Paragraf/başlık içeriği: boşluk (``text:s``), sekme, satır sonu; notlar/yorumlar hariç."""
    parts: list[str] = []

    def visit(node: Element) -> None:
        if node.text:
            parts.append(node.text)
        for child in node:
            tag = child.tag
            if tag == f"{_TEXT}s":
                parts.append(" " * min(_count(child.get(f"{_TEXT}c")), 100))
            elif tag == f"{_TEXT}tab":
                parts.append(" ")
            elif tag == f"{_TEXT}line-break":
                parts.append("\n")
            elif tag not in _SKIP_INLINE and not str(tag).startswith("{urn:oasis:names:tc:opendocument:xmlns:drawing"):
                visit(child)
            if child.tail:
                parts.append(child.tail)

    visit(element)
    return squeeze("".join(parts))


def _block_text(container: Element) -> str:
    """Hücre içeriği: paragraflar, listeler ve iç tablolar düz metin olarak."""
    parts = []
    for child in container:
        if child.tag in (f"{_TEXT}p", f"{_TEXT}h"):
            parts.append(inline_text(child))
        elif child.tag in _CONTAINERS or child.tag == f"{_TABLE}table":
            parts.append(_block_text(child))
        elif child.tag in (f"{_TABLE}table-row", f"{_TABLE}table-cell", f"{_TABLE}table-rows",
                           f"{_TABLE}table-header-rows", f"{_TABLE}table-row-group"):
            parts.append(_block_text(child))
    return " ".join(" ".join(p.split()) for p in parts if p.strip())


def _row_elements(table: Element) -> Iterator[Element]:
    for child in table:
        if child.tag == f"{_TABLE}table-row":
            yield child
        elif child.tag in (f"{_TABLE}table-header-rows", f"{_TABLE}table-rows", f"{_TABLE}table-row-group"):
            yield from _row_elements(child)


def _cells(row: Element) -> list[str]:
    cells: list[str] = []
    empty = 0
    for cell in row:
        if cell.tag not in (f"{_TABLE}table-cell", f"{_TABLE}covered-table-cell"):
            continue
        repeat = _count(cell.get(f"{_TABLE}number-columns-repeated"))
        text = _block_text(cell) if cell.tag == f"{_TABLE}table-cell" else ""
        if not text:
            empty += repeat
            continue
        if empty:
            cells.extend([""] * min(empty, _MAX_COLUMNS - len(cells)))
            empty = 0
        cells.extend([text] * min(repeat, _MAX_COLUMNS - len(cells)))
        if len(cells) >= _MAX_COLUMNS:
            break
    return cells


def table_rows(table: Element, limit: int = MAX_ROWS_PER_SHEET + 1) -> list[list[str]]:
    """Satırlar; boş satırlar yalnız arkasından dolu satır gelirse üretilir (satır numarası korunur)."""
    rows: list[list[str]] = []
    empty = 0
    for row in _row_elements(table):
        repeat = _count(row.get(f"{_TABLE}number-rows-repeated"))
        cells = _cells(row)
        if not any(cell.strip() for cell in cells):
            empty += repeat
            continue
        if empty:
            rows.extend([] for _ in range(max(0, min(empty, limit - len(rows)))))
            empty = 0
        rows.extend(list(cells) for _ in range(max(0, min(repeat, limit - len(rows)))))
        if len(rows) >= limit:
            break
    return rows


# --------------------------------------------------------------------------- #
# .odt
# --------------------------------------------------------------------------- #


def _walk(container: Element, items: list[Item]) -> None:
    for child in container:
        tag = child.tag
        if tag == f"{_TEXT}h":
            text = inline_text(child)
            if text:
                items.append(Item("heading", text))
        elif tag == f"{_TEXT}p":
            text = inline_text(child)
            if text:
                items.append(Item("paragraph", text))
        elif tag == f"{_TABLE}table":
            items.append(Item("table", rows=table_rows(child, limit=MAX_ROWS_PER_SHEET)))
        elif tag in _CONTAINERS:
            _walk(child, items)
        # içindekiler, izlenen değişiklikler, formlar, bildirimler ve çizimler bilerek atlanır


def odt_items(path: Path) -> list[Item]:
    body = _content(path).find(f"{_OFFICE}body/{_OFFICE}text")
    if body is None:
        raise FormatError("OpenDocument metin belgesi değil (office:text yok)")
    items: list[Item] = []
    _walk(body, items)
    return items


def read_odt(builder: ModelBuilder, report: DocReport, path: Path) -> Leftovers:
    try:
        items = odt_items(path)
    except FormatError as exc:
        return fail(report, f"ODT okunamadı: {exc}")
    return read_items(builder, report, path.name, items, table_reader="odt_tablo")


def preview_odt(path: Path) -> list[Block]:
    return preview_items(odt_items(path))


# --------------------------------------------------------------------------- #
# .ods
# --------------------------------------------------------------------------- #


def ods_sheets(path: Path, report: DocReport | None = None) -> list[Sheet]:
    body = _content(path).find(f"{_OFFICE}body/{_OFFICE}spreadsheet")
    if body is None:
        raise FormatError("OpenDocument hesap tablosu değil (office:spreadsheet yok)")
    sheets: list[Sheet] = []
    for number, table in enumerate(body.findall(f"{_TABLE}table"), start=1):
        title = table.get(f"{_TABLE}name") or f"Sayfa{number}"
        sheets.append((title, cap_rows(title, table_rows(table), report)))
    return sheets


def read_ods(builder: ModelBuilder, report: DocReport, path: Path) -> Leftovers:
    try:
        sheets = ods_sheets(path, report)
    except FormatError as exc:
        return fail(report, f"ODS okunamadı: {exc}")
    return read_sheets(builder, report, path.name, sheets)


def preview_ods(path: Path) -> list[Block]:
    return preview_sheets(ods_sheets(path))


register(FormatSpec("odt", "OpenDocument metin", (".odt",), read_odt, preview_odt,
                    description="LibreOffice/OpenOffice belgeleri: başlıklar, paragraflar, tablolar",
                    sample="ornek_bicimler/kart_tasarim.odt"))
register(FormatSpec("ods", "OpenDocument tablo", (".ods",), read_ods, preview_ods, group="Tablo",
                    description="LibreOffice Calc: STTM/eşleme ve job tabloları, Excel ile aynı anlam",
                    sample="ornek_bicimler/kart_sttm.ods"))

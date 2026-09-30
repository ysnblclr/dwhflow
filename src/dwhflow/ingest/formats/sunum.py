"""PowerPoint (``.pptx``): slayt metinleri, tablolar ve konuşmacı notları (zip + XML, ek paket yok).

Slayt sırası ``presentation.xml``'deki sıradır (dosya adları değil). Her slaytta şekiller
belge sırasıyla gezilir (gruplar dahil); tarih/slayt no/alt bilgi yer tutucuları atlanır.

Konumlar: ``slayt 3 · paragraf 2`` (slayttaki metin paragrafları, başlık dahil),
``slayt 3 · tablo 1`` (satırlar ``… · satır n``), ``slayt 3 · not 1`` (konuşmacı notu
paragrafları). Tablolar tablo yorumlayıcısına, paragraflar metin kurallarına gider.
``.ppsx``/``.pptm`` de aynı yapıdadır; makrolar asla çalıştırılmaz. Grafik/SmartArt içerikleri okunmaz.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree.ElementTree import Element

from ..base import DocReport, ModelBuilder
from ..preview import MAX_BLOCKS, _table_block
from ..registry import Block, FormatSpec, register
from ..tables import interpret, rows_to_view
from ..text import extract_paragraphs
from ._ortak import FormatError, Leftovers, fail, member_xml, open_zip, squeeze, zip_has, zip_rels

_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_SKIP_PLACEHOLDERS = {"dt", "sldNum", "ftr", "hdr", "sldImg"}
MAX_SLIDES = 2000


@dataclass
class Slide:
    number: int
    title: str = ""
    paragraphs: list[str] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _slide_parts(archive: zipfile.ZipFile) -> list[str]:
    names = set(archive.namelist())
    ordered: list[str] = []
    if "ppt/presentation.xml" in names:
        rels = zip_rels(archive, "ppt/presentation.xml")
        root = member_xml(archive, "ppt/presentation.xml")
        for slide_id in root.iter(f"{_P}sldId"):
            kind, target = rels.get(slide_id.get(f"{_R}id") or "", ("", ""))
            if kind == "slide" and target in names:
                ordered.append(target)
    if not ordered:  # sunum dosyası eksik/bozuk: dosya adı sırası
        found = [n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        ordered = sorted(found, key=lambda n: int(re.findall(r"\d+", n)[-1]))
    return ordered[:MAX_SLIDES]


def _paragraph_text(paragraph: Element) -> str:
    parts = []
    for node in paragraph.iter():
        if node.tag == f"{_A}t" and node.text:
            parts.append(node.text)
        elif node.tag == f"{_A}br":
            parts.append("\n")
    return squeeze("".join(parts))


def _placeholder(shape: Element) -> str | None:
    for tag in (f"{_P}nvSpPr", f"{_P}nvGraphicFramePr"):
        props = shape.find(tag)
        if props is not None:
            holder = props.find(f"{_P}nvPr/{_P}ph")
            if holder is not None:
                return holder.get("type") or "body"
    return None


def _table_rows(table: Element) -> list[list[str]]:
    rows = []
    for row in table.iter(f"{_A}tr"):
        cells = []
        for cell in row.findall(f"{_A}tc"):
            text = " ".join(_paragraph_text(p) for p in cell.iter(f"{_A}p"))
            cells.append(" ".join(text.split()))
        rows.append(cells)
    return rows


def _walk(container: Element, slide: Slide, into_notes: bool) -> None:
    for shape in container:
        tag = shape.tag
        if tag == f"{_P}grpSp":
            _walk(shape, slide, into_notes)
        elif tag == f"{_P}sp":
            kind = _placeholder(shape)
            if kind in _SKIP_PLACEHOLDERS:
                continue
            body = shape.find(f"{_P}txBody")
            if body is None:
                continue
            texts = [t for t in (_paragraph_text(p) for p in body.findall(f"{_A}p")) if t]
            if into_notes:
                slide.notes.extend(texts)
            else:
                if kind in ("title", "ctrTitle") and texts and not slide.title:
                    slide.title = " ".join(texts)
                slide.paragraphs.extend(texts)
        elif tag == f"{_P}graphicFrame" and not into_notes:
            for table in shape.iter(f"{_A}tbl"):
                slide.tables.append(_table_rows(table))


def slides(path: Path) -> list[Slide]:
    with open_zip(path) as archive:
        parts = _slide_parts(archive)
        if not parts:
            raise FormatError("sunumda slayt bulunamadı (.pptx olmalı)")
        out = []
        for number, part in enumerate(parts, start=1):
            slide = Slide(number)
            tree = member_xml(archive, part).find(f"{_P}cSld/{_P}spTree")
            if tree is not None:
                _walk(tree, slide, into_notes=False)
            for kind, target in zip_rels(archive, part).values():
                if kind == "notesSlide" and zip_has(archive, target):
                    notes = member_xml(archive, target).find(f"{_P}cSld/{_P}spTree")
                    if notes is not None:
                        _walk(notes, slide, into_notes=True)
            out.append(slide)
        return out


def read_pptx(builder: ModelBuilder, report: DocReport, path: Path) -> Leftovers:
    try:
        deck = slides(path)
    except FormatError as exc:
        return fail(report, f"PowerPoint dosyası okunamadı: {exc}")
    paragraphs: list[tuple[str, str]] = []
    for slide in deck:
        prefix = f"slayt {slide.number}"
        paragraphs += [(f"{prefix} · paragraf {i}", text) for i, text in enumerate(slide.paragraphs, 1)]
        for number, rows in enumerate(slide.tables, start=1):
            view = rows_to_view(rows, locator=f"{prefix} · tablo {number}", row_locator="satır {n}")
            if view:
                report.flows += interpret(view, builder, report, file=path.name, reader="pptx_tablo")
        paragraphs += [(f"{prefix} · not {i}", text) for i, text in enumerate(slide.notes, 1)]
    result = extract_paragraphs(paragraphs, builder, report, file=path.name)
    report.flows += result.flows
    return result.leftovers


def preview_pptx(path: Path) -> list[Block]:
    blocks: list[Block] = []
    for slide in slides(path):
        prefix = f"slayt {slide.number}"
        blocks.append({"locator": prefix, "kind": "heading",
                       "text": f"Slayt {slide.number}" + (f" — {slide.title}" if slide.title else "")})
        blocks += [{"locator": f"{prefix} · paragraf {i}", "kind": "paragraph", "text": text}
                   for i, text in enumerate(slide.paragraphs, 1)]
        for number, rows in enumerate(slide.tables, start=1):
            block = _table_block(rows, f"{prefix} · tablo {number}")
            if block:
                blocks.append(block)
        blocks += [{"locator": f"{prefix} · not {i}", "kind": "paragraph", "text": text}
                   for i, text in enumerate(slide.notes, 1)]
        if len(blocks) >= MAX_BLOCKS:
            break
    return blocks[:MAX_BLOCKS]


register(FormatSpec("pptx", "PowerPoint", (".pptx", ".ppsx", ".pptm"), read_pptx, preview_pptx,
                    description="Slayt metinleri, tablolar ve konuşmacı notları",
                    sample="ornek_bicimler/kart_mimari_sunum.pptx"))

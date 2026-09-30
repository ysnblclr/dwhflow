"""Visio (``.vsdx``, ``.vsdm``): sayfalardaki şekiller ve bağlayıcılar (zip + XML, ek paket yok).

* Sayfalar ``visio/pages/pages.xml`` sırasıyla okunur (``sayfa n``); arka plan sayfaları atlanır.
* Şekil metni ``<Text>`` öğesinden; gruplardaki alt şekiller de gezilir.
* Bağlantılar ``<Connects>``: bağlayıcının ``BeginX`` ucu kaynak, ``EndX`` ucu hedeftir. Ok
  başı yalnız başlangıç ucundaysa (``BeginArrow`` var, ``EndArrow`` yok) yön ters çevrilir.
  Bir uç metinsiz alt şekle bağlıysa metni olan en yakın üst şekil alınır.
* Kalıplar (``visio/masters/masters.xml``) şekli sınıflandırır: veritabanı/veri deposu/belge
  → veri nesnesi; işlem/alt süreç → süreç; kapsayıcı/kulvar/başlık/açıklama balonu/başla-bitir
  düğüm sayılmaz; "metin/açıklama" şekilleri metin kurallarından geçer. Kalıp adı yoksa ortak
  ad kuralı uygulanır (bkz. ``_diyagram``). Makrolar asla çalıştırılmaz.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree.ElementTree import Element

from ..base import DocReport, ModelBuilder, fold
from ..registry import Block, FormatSpec, register
from . import _diyagram as diagram
from ._ortak import FormatError, Leftovers, fail, member_xml, open_zip, squeeze, zip_has, zip_rels

_V = "{http://schemas.microsoft.com/office/visio/2012/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
MAX_SHAPES = 20000

CONNECTOR = "connector"
#: Kalıp/şekil adındaki sözcükler (``fold`` edilmiş, tam sözcük/öbek) → sınıf; ilk tutan kazanır.
_KINDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (CONNECTOR, ("connector", "baglayici", "baglanti")),
    (diagram.SKIP, ("container", "kapsayici", "swimlane", "kulvar", "title", "baslik", "legend", "gosterge",
                    "terminator", "sonlandirici", "start end", "basla bitir", "page", "sayfa")),
    (diagram.TEXT, ("text", "metin", "annotation", "aciklama", "comment", "yorum", "callout", "belirtme")),
    (diagram.DATASET, ("database", "veritabani", "data store", "veri deposu", "stored data", "depolanan veri",
                       "document", "belge", "disk", "cylinder", "silindir", "file", "dosya", "table", "tablo")),
    (diagram.PROCESS, ("process", "islem", "surec", "subprocess", "alt islem", "predefined process",
                       "onceden tanimli islem", "task", "gorev", "job")),
)
_INSTANCE = re.compile(r"\.\d+\b")  # "Database.12" → "Database"


def _classify(kind_text: str) -> str | None:
    words = f" {fold(_INSTANCE.sub(' ', kind_text))} "
    for result, phrases in _KINDS:
        if any(f" {phrase} " in words for phrase in phrases):
            return result
    return None


@dataclass
class _Shape:
    id: str
    label: str
    kind: str | None
    parent: str | None
    begin_arrow: bool
    end_arrow: bool


def _cell(shape: Element, name: str) -> str | None:
    for cell in shape.findall(f"{_V}Cell"):
        if cell.get("N") == name:
            return cell.get("V")
    return None


def _arrow(value: str | None) -> bool:
    try:
        return float(value or 0) != 0
    except ValueError:
        return False


def _masters(archive: zipfile.ZipFile) -> dict[str, str]:
    name = "visio/masters/masters.xml"
    if not zip_has(archive, name):
        return {}
    return {master.get("ID") or "": f"{master.get('NameU') or ''} {master.get('Name') or ''}"
            for master in member_xml(archive, name).iter(f"{_V}Master")}


def _shapes(container: Element, masters: dict[str, str], parent: str | None, master: str | None,
            out: dict[str, _Shape]) -> None:
    for shape in container.findall(f"{_V}Shapes/{_V}Shape"):
        if len(out) >= MAX_SHAPES:
            return
        shape_id = shape.get("ID") or ""
        own_master = shape.get("Master") or master
        kind_text = " ".join([masters.get(own_master or "", ""), shape.get("NameU") or "", shape.get("Name") or ""])
        text = shape.find(f"{_V}Text")
        out[shape_id] = _Shape(shape_id, squeeze("".join(text.itertext())) if text is not None else "",
                               _classify(kind_text), parent, _arrow(_cell(shape, "BeginArrow")),
                               _arrow(_cell(shape, "EndArrow")))
        _shapes(shape, masters, shape_id, own_master, out)


def _page(number: int, title: str, content: Element, masters: dict[str, str]) -> diagram.Page:
    page = diagram.Page(prefix=f"sayfa {number}", title=title)
    shapes: dict[str, _Shape] = {}
    _shapes(content, masters, None, None, shapes)
    begins: dict[str, str] = {}
    ends: dict[str, str] = {}
    for connect in content.iter(f"{_V}Connect"):
        source, cell, target = connect.get("FromSheet"), connect.get("FromCell"), connect.get("ToSheet")
        if source and target and cell == "BeginX":
            begins[source] = target
        elif source and target and cell == "EndX":
            ends[source] = target
    connectors = set(begins) | set(ends)

    def labelled(shape_id: str | None) -> str | None:
        seen = set()
        while shape_id and shape_id in shapes and shape_id not in seen:
            seen.add(shape_id)
            if shapes[shape_id].label and shape_id not in connectors:
                return shape_id
            shape_id = shapes[shape_id].parent
        return None

    for shape in shapes.values():
        if shape.id in connectors or shape.kind == CONNECTOR or not shape.label:
            continue
        page.node(shape.id, shape.label, shape.kind)
    for connector in sorted(connectors, key=lambda c: int(c) if c.isdigit() else 0):
        source, target = labelled(begins.get(connector)), labelled(ends.get(connector))
        if not source or not target:
            continue
        line = shapes.get(connector)
        if line is not None and line.begin_arrow and not line.end_arrow:
            source, target = target, source
        page.links.append(diagram.Link(source, target, line.label if line else ""))
    return page


def visio_pages(path: Path) -> list[diagram.Page]:
    with open_zip(path) as archive:
        index = "visio/pages/pages.xml"
        if not zip_has(archive, index):
            raise FormatError("Visio çizimi değil (visio/pages/pages.xml yok; .vsd eski biçimi desteklenmez)")
        masters = _masters(archive)
        rels = zip_rels(archive, index)
        pages = []
        for element in member_xml(archive, index).iter(f"{_V}Page"):
            if element.get("Background") == "1":
                continue
            rel = element.find(f"{_V}Rel")
            _, target = rels.get(rel.get(f"{_R}id") or "" if rel is not None else "", ("", ""))
            if not target or not zip_has(archive, target):
                continue
            title = element.get("Name") or element.get("NameU") or ""
            pages.append(_page(len(pages) + 1, title, member_xml(archive, target), masters))
        return pages


def read_visio(builder: ModelBuilder, report: DocReport, path: Path) -> Leftovers:
    try:
        pages = visio_pages(path)
    except FormatError as exc:
        return fail(report, f"Visio çizimi okunamadı: {exc}")
    return diagram.apply(builder, report, path.name, pages, extractor="diyagram:visio")


def preview_visio(path: Path) -> list[Block]:
    return diagram.preview(visio_pages(path))


register(FormatSpec("visio", "Visio", (".vsdx", ".vsdm"), read_visio, preview_visio, group="Diyagram",
                    description="Şekiller → tablo/job, bağlayıcılar → akış; kalıp adları şekli sınıflandırır",
                    sample="ornek_bicimler/kart_akis.vsdx"))

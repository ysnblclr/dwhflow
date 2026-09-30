"""draw.io / diagrams.net: ``.drawio``, ``.dio`` ve içerikten tanınan ``.xml`` (``<mxfile``/``<mxGraphModel``).

Her ``<diagram>`` bir sayfadır (``sayfa n``). Sıkıştırılmış sayfalar açılır: base64 → ham
deflate → URL kod çözme (boyut sınırlı). Şekiller (``vertex``) ve oklar (``edge`` +
``source``/``target``) ortak diyagram kuralıyla modele işlenir (bkz. ``_diyagram``).

Şekil ipuçları (``style``): ``shape=cylinder…``, ``datastore``, ``database``, ``document``,
``note`` … → veri nesnesi; ``process``, ``predefinedProcess`` → süreç; ``text`` → serbest
metin (metin kurallarından geçer); kulvar/grup/kapsayıcılar düğüm sayılmaz. Açık ipucu için
stile ``tur=surec`` ya da ``tur=veri`` eklenebilir (``UserObject`` özniteliği ``tur`` da olur).
Okun etiketi (``value`` ya da ok üstündeki etiket şekli) bir job adıysa akış o süreçten geçer.
"""

from __future__ import annotations

import base64
import binascii
import re
import urllib.parse
import zlib
from pathlib import Path
from xml.etree.ElementTree import Element

from ..base import DocReport, ModelBuilder, fold
from ..registry import Block, FormatSpec, register
from . import _diyagram as diagram
from ._ortak import (
    MAX_TEXT_FILE_BYTES,
    FormatError,
    Leftovers,
    fail,
    html_to_text,
    local,
    parse_xml,
    read_limited,
    squeeze,
)

MAX_INFLATED = 40 * 1024 * 1024
_DATA_SHAPES = ("cylinder", "datastore", "database", "stored_data", "storeddata", "internal_storage",
                "data_storage", "document", "disk", "tape", "note", "card")
_PROCESS_SHAPES = ("process", "predefined")
_PROCESS_WORDS = {"surec", "process", "job", "is", "gorev", "task", "etl"}
_DATA_WORDS = {"veri", "dataset", "tablo", "table", "dosya", "file", "data", "db"}
_ROOT = re.compile(rb"^(?:<\?xml[^>]*>\s*)?(?:<!--.*?-->\s*)*<(?:mxfile|mxGraphModel)\b", re.DOTALL)


def looks_like_drawio(head: bytes, name: str) -> bool:
    return bool(_ROOT.match(head[:65536].lstrip(b"\xef\xbb\xbf \t\r\n")))


def _inflate(payload: str) -> Element:
    try:
        raw = base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise FormatError(f"sıkıştırılmış sayfa çözülemedi: {exc}") from exc
    try:
        inflater = zlib.decompressobj(-15)
        data = inflater.decompress(raw, MAX_INFLATED)
        if inflater.unconsumed_tail:
            raise FormatError("sıkıştırılmış sayfa çok büyük")
    except zlib.error:
        data = raw  # bazı sürümler yalnız base64 yazar
    text = data.decode("utf-8", errors="replace")
    if "%3C" in text[:64] or "%3c" in text[:64]:
        text = urllib.parse.unquote(text)
    if not text.lstrip().startswith("<"):
        raise FormatError("sıkıştırılmış sayfa XML değil")
    return parse_xml(text)


def _style(value: str) -> tuple[set[str], dict[str, str]]:
    names: set[str] = set()
    pairs: dict[str, str] = {}
    for token in (value or "").split(";"):
        token = token.strip()
        if not token:
            continue
        if "=" in token:
            key, _, val = token.partition("=")
            pairs[key.strip().lower()] = val.strip().lower()
        else:
            names.add(token.lower())
    return names, pairs


def _explicit(value: str | None) -> str | None:
    word = fold(value or "")
    if word in _PROCESS_WORDS:
        return diagram.PROCESS
    if word in _DATA_WORDS:
        return diagram.DATASET
    return None


def _hint(names: set[str], pairs: dict[str, str], explicit: str | None) -> str | None:
    hint = _explicit(explicit) or _explicit(pairs.get("tur"))
    if hint:
        return hint
    shape = " ".join([pairs.get("shape", ""), *names])
    if "text" in names or pairs.get("shape") == "text" or "edgelabel" in names:
        return diagram.TEXT
    if "swimlane" in shape or "group" in names or pairs.get("container") == "1":
        return diagram.SKIP
    if any(word in shape for word in _DATA_SHAPES):
        return diagram.DATASET
    if any(word in shape for word in _PROCESS_SHAPES):
        return diagram.PROCESS
    return None


def _label(raw: str, pairs: dict[str, str]) -> str:
    """``html=1`` stilinde etiket HTML'dir; değilse düz metindir (``<`` gibi karakterler korunur)."""
    return html_to_text(raw) if pairs.get("html") == "1" else squeeze(raw)


def _page(number: int, title: str, model: Element) -> diagram.Page:
    page = diagram.Page(prefix=f"sayfa {number}", title=title)
    root = model.find("root")
    cells: list[dict] = []
    for element in root if root is not None else []:
        tag = local(element.tag)
        if tag == "mxCell":
            attrs, label, explicit = dict(element.attrib), element.get("value") or "", None
        elif tag in ("UserObject", "object"):
            inner = element.find("mxCell")
            if inner is None:
                continue
            attrs = {**inner.attrib, "id": element.get("id") or inner.get("id") or ""}
            label = element.get("label") or ""
            if element.get("placeholders") == "1":
                values = dict(element.attrib)
                label = re.sub(r"%(\w+)%", lambda m, v=values: v.get(m.group(1), m.group(0)), label)
            explicit = element.get("tur")
        else:
            continue
        names, pairs = _style(attrs.get("style", ""))
        cells.append({"id": attrs.get("id", ""), "parent": attrs.get("parent", ""),
                      "vertex": attrs.get("vertex") == "1", "edge": attrs.get("edge") == "1",
                      "source": attrs.get("source"), "target": attrs.get("target"),
                      "label": _label(label, pairs), "hint": _hint(names, pairs, explicit)})
    edges = {cell["id"]: cell for cell in cells if cell["edge"]}
    parents = {cell["parent"] for cell in cells if cell["vertex"]}
    edge_labels: dict[str, list[str]] = {}
    for cell in cells:
        if not cell["vertex"]:
            continue
        if cell["parent"] in edges:  # okun üstündeki etiket
            edge_labels.setdefault(cell["parent"], []).append(cell["label"])
            continue
        hint = diagram.SKIP if cell["id"] in parents and cell["hint"] != diagram.TEXT else cell["hint"]
        page.node(cell["id"], cell["label"], hint)
    for edge in edges.values():
        if edge["source"] and edge["target"]:
            label = "\n".join(t for t in [edge["label"], *edge_labels.get(edge["id"], [])] if t)
            page.links.append(diagram.Link(edge["source"], edge["target"], label))
    return page


def drawio_pages(path: Path) -> list[diagram.Page]:
    root = parse_xml(read_limited(path, MAX_TEXT_FILE_BYTES))
    tag = local(root.tag)
    if tag == "mxGraphModel":
        models = [("", root)]
    elif tag == "mxfile":
        models = []
        for diagram_element in root.findall("diagram"):
            model = diagram_element.find("mxGraphModel")
            if model is None and (diagram_element.text or "").strip():
                model = _inflate(diagram_element.text.strip())
            if model is not None:
                models.append((diagram_element.get("name") or "", model))
    else:
        raise FormatError("draw.io dosyası değil (kök mxfile/mxGraphModel olmalı)")
    return [_page(number, title, model) for number, (title, model) in enumerate(models, start=1)]


def read_drawio(builder: ModelBuilder, report: DocReport, path: Path) -> Leftovers:
    try:
        pages = drawio_pages(path)
    except FormatError as exc:
        return fail(report, f"draw.io diyagramı okunamadı: {exc}")
    return diagram.apply(builder, report, path.name, pages, extractor="diyagram:drawio")


def preview_drawio(path: Path) -> list[Block]:
    return diagram.preview(drawio_pages(path))


register(FormatSpec("drawio", "draw.io", (".drawio", ".dio"), read_drawio, preview_drawio, sniff=looks_like_drawio,
                    group="Diyagram", description="Şekiller → tablo/job, oklar → akış; sıkıştırılmış sayfalar dahil",
                    sample="ornek_bicimler/kart_akis.drawio"))

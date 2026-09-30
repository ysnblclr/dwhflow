"""HTML → doküman öğeleri (standart kütüphane ``html.parser``; betik çalıştırılmaz).

* ``h1``–``h6`` başlık olur ve sonraki paragrafların konum etiketine girer.
* Paragraflar: ``p``, ``div``, ``li``, ``pre`` … blok öğeleri; ``br`` satır sonu.
* ``script``, ``style``, ``head``, ``svg`` … içeriği yok sayılır.
* Tablolar: en az 2 satır ve 2 sütunlu tablo **veri tablosu** sayılır ve tablo
  yorumlayıcısına gider (``rowspan`` birleşik hücre alttaki satırlara kopyalanır,
  ``colspan`` boş hücrelerle hizalanır). Tek satırlı/tek sütunlu tablolar (e-posta ve
  Confluence sayfa düzeni) **düzen tablosu** sayılır; içindeki paragraflar normal akışa döner.
* MHTML (``.mht``; Confluence'ın "Word'e aktar" çıktısı olan ``.doc``) içinden HTML parçası alınır.
"""

from __future__ import annotations

import codecs
import email
import email.policy
import re
from html.parser import HTMLParser

from ..readers import _decode
from ._ortak import Item, squeeze

_SKIP = {"script", "style", "noscript", "template", "title", "svg", "math", "iframe", "object",
         "embed", "canvas", "select", "button", "head"}
_BLOCK = {"p", "div", "section", "article", "header", "footer", "main", "aside", "nav", "blockquote",
          "pre", "ul", "ol", "li", "dl", "dt", "dd", "figure", "figcaption", "address", "form",
          "fieldset", "details", "summary", "hr", "center", "caption", "body", "html"}
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_MAX_SPAN = 50


class _Box:
    """Akış kabı: belgenin kendisi ya da bir tablo hücresi."""

    def __init__(self) -> None:
        self.items: list[Item] = []
        self.buffer: list[str] = []

    def text(self) -> str:
        parts = []
        for item in self.items:
            if item.kind == "table":
                parts.extend(" ".join(cell for cell in row if cell) for row in item.rows)
            else:
                parts.append(item.text)
        return " ".join(" ".join(p.split()) for p in parts if p.strip())


class _Table:
    def __init__(self, parent: _Box) -> None:
        self.parent = parent
        self.rows: list[list[tuple[str, _Box | None]]] = []
        self.row: list[tuple[str, _Box | None]] | None = None
        self.cell: _Box | None = None
        self.cell_span = (1, 1)
        #: sütun → [kalan satır, metin] (rowspan)
        self.carry: dict[int, list] = {}
        self.new_carry: dict[int, list] = {}
        self.pending: list[int] = []

    def start_row(self) -> None:
        self.end_row()
        self.row = []
        self.pending = sorted(self.carry)

    def _consume_carry(self, upto: int | None) -> None:
        """Bu satırda sırası gelen rowspan hücrelerini yerleştirir."""
        assert self.row is not None
        while self.pending and (upto is None or self.pending[0] <= len(self.row)):
            column = self.pending.pop(0)
            while len(self.row) < column:
                self.row.append(("", None))
            remaining, text = self.carry[column]
            self.row.append((text, None))
            if remaining <= 1:
                del self.carry[column]
            else:
                self.carry[column] = [remaining - 1, text]

    def place_cell(self, text: str, box: _Box, rowspan: int, colspan: int) -> None:
        if self.row is None:
            self.start_row()
        assert self.row is not None
        self._consume_carry(len(self.row))
        column = len(self.row)
        self.row.append((text, box))
        self.row.extend(("", None) for _ in range(1, colspan))
        if rowspan > 1:
            for offset in range(colspan):
                self.new_carry[column + offset] = [rowspan - 1, text if offset == 0 else ""]
        self._consume_carry(len(self.row))

    def end_row(self) -> None:
        if self.row is None:
            return
        self._consume_carry(None)
        if self.row:
            self.rows.append(self.row)
        self.carry.update(self.new_carry)
        self.new_carry = {}
        self.row = None


def _int(value: str | None, default: int = 1) -> int:
    try:
        return max(1, min(int(str(value).strip()), _MAX_SPAN))
    except (TypeError, ValueError):
        return default


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Box()
        self.boxes: list[_Box] = [self.root]
        self.tables: list[_Table] = []
        self.skip: list[str] = []
        self.pre = 0
        self.heading: str | None = None

    # -- yardımcılar -------------------------------------------------------- #

    @property
    def box(self) -> _Box:
        return self.boxes[-1]

    def flush(self) -> None:
        box = self.box
        text = squeeze("".join(box.buffer))
        box.buffer = []
        if not text:
            return
        if self.heading is not None:
            box.items.append(Item("heading", " ".join(text.split())))
        else:
            box.items.append(Item("paragraph", text))

    def close_cell(self) -> None:
        if not self.tables:
            return
        table = self.tables[-1]
        if table.cell is None:
            return
        self.flush()
        cell = table.cell
        while len(self.boxes) > 1 and self.boxes[-1] is not cell:
            self.boxes.pop()
        if len(self.boxes) > 1:
            self.boxes.pop()
        rowspan, colspan = table.cell_span
        table.place_cell(cell.text(), cell, rowspan, colspan)
        table.cell = None

    def close_table(self) -> None:
        self.close_cell()
        table = self.tables.pop()
        table.end_row()
        rows = [row for row in table.rows if any(text.strip() for text, _ in row)]
        width = max((len(row) for row in rows), default=0)
        target = table.parent
        if len(rows) >= 2 and width >= 2:
            target.items.append(Item("table", rows=[[text for text, _ in row] for row in rows]))
        else:  # düzen tablosu: hücre içerikleri akışa döner
            for row in table.rows:
                for _, box in row:
                    if box is not None:
                        target.items.extend(box.items)

    # -- HTMLParser --------------------------------------------------------- #

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "body":
            self.skip = []  # kapatılmamış <head>/<title> gövdeyi yutmasın
        if self.skip:
            if tag in _SKIP:
                self.skip.append(tag)
            return
        if tag in _SKIP:
            self.skip.append(tag)
            return
        if tag == "br":
            self.box.buffer.append("\n")
        elif tag in _HEADINGS:
            self.flush()
            self.heading = tag
        elif tag == "table":
            self.flush()
            self.tables.append(_Table(self.box))
        elif tag == "tr" and self.tables:
            self.close_cell()
            self.tables[-1].start_row()
        elif tag in ("td", "th") and self.tables:
            self.close_cell()
            table = self.tables[-1]
            if table.row is None:
                table.start_row()
            values = dict(attrs)
            table.cell_span = (_int(values.get("rowspan")), _int(values.get("colspan")))
            table.cell = _Box()
            self.boxes.append(table.cell)
        elif tag in _BLOCK:
            self.flush()
            if tag == "pre":
                self.pre += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skip or tag in _SKIP:
            return
        if tag == "br":
            self.box.buffer.append("\n")
        elif tag in _BLOCK:
            self.flush()

    def handle_endtag(self, tag: str) -> None:
        if self.skip:
            if tag in self.skip:
                while self.skip and self.skip.pop() != tag:
                    pass
            return
        if tag in _HEADINGS and self.heading is not None:
            self.flush()
            self.heading = None
        elif tag in ("td", "th"):
            self.close_cell()
        elif tag == "tr" and self.tables:
            self.close_cell()
            self.tables[-1].end_row()
        elif tag == "table" and self.tables:
            self.flush()
            self.close_table()
        elif tag in _BLOCK:
            self.flush()
            if tag == "pre" and self.pre:
                self.pre -= 1

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        if self.pre:
            self.box.buffer.append(data)
        else:
            self.box.buffer.append(re.sub(r"\s+", " ", data))

    def finish(self) -> list[Item]:
        self.close()
        self.flush()
        while self.tables:
            self.close_table()
        self.heading = None
        self.boxes = [self.root]
        self.flush()
        return self.root.items


def html_items(text: str) -> list[Item]:
    parser = _Parser()
    parser.feed(text)
    return parser.finish()


# --------------------------------------------------------------------------- #
# Kodlama ve MHTML
# --------------------------------------------------------------------------- #

_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.IGNORECASE)


def decode_html(data: bytes) -> str:
    """BOM → ``<meta charset>`` → UTF-8 / cp1254 / latin-1."""
    if data.startswith(codecs.BOM_UTF8):
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", errors="replace")
    match = _META_CHARSET.search(data[:8192])
    if match:
        try:
            return data.decode(match.group(1).decode("ascii"), errors="replace")
        except (LookupError, UnicodeDecodeError):
            pass
    return _decode(data)


def looks_like_mime(data: bytes) -> bool:
    head = data[:8192].lstrip()
    return bool(re.search(rb"(?im)^mime-version\s*:", head)) and bool(
        re.search(rb"(?im)^content-type\s*:\s*multipart/", head))


def mime_html(data: bytes) -> str | None:
    """MHTML (çok parçalı MIME) içindeki ilk ``text/html`` parçasının metni."""
    message = email.message_from_bytes(data, policy=email.policy.default)
    for part in message.walk():
        if part.get_content_type() != "text/html":
            continue
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset()
        if charset:
            try:
                return payload.decode(charset, errors="replace")
            except LookupError:
                pass
        return decode_html(payload)
    return None


_HTML_START = ("<!doctype html", "<html", "<head", "<body", "<table", "<meta")


def looks_like_html(data: bytes) -> bool:
    """Uzantısı farklı (``.doc``, ``.xls``) ama içeriği HTML olan dosyalar (web dışa aktarımları)."""
    head = decode_html(data[:8192]).lstrip("﻿ \t\r\n").lower()
    if head.startswith("<?xml"):
        return "<html" in head
    return head.startswith(_HTML_START)

"""Mermaid akış şemaları (``.mmd``, ``.mermaid``; ``flowchart``/``graph`` ile başlayan ``.txt``).

Desteklenen sözdizimi: düğüm tanımları (``A[STG_X]``, ``A[(DB)]`` silindir, ``A[[JOB]]`` alt
süreç, ``A(…)``, ``A{…}``, ``A>…]``, ``A@{ shape: cyl, label: "…" }``), bağlantılar ``-->``,
``---``, ``-.->``, ``==>``, ``--o``/``--x``, uzunlukları farklı oklar, etiketler (``-->|LOAD_JOB|``,
``-- LOAD_JOB -->``), zincirler (``A --> B --> C``), gruplar (``A & B --> C``), ``;`` ayırıcısı,
``%%`` yorumları, ``---`` ön bilgisi, ``subgraph``/``end``, ``class``/``:::sınıf`` ipuçları
(``surec``/``veri``). ``~~~`` görünmez bağlantıdır, akış sayılmaz. Silindir → veri nesnesi,
``[[…]]`` → süreç; sınıflandırma ve etiket kuralı ortak diyagram kuralıdır (``_diyagram``).
Yalnız ``flowchart``/``graph`` desteklenir; diğer Mermaid türleri "desteklenmiyor" raporlanır.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..base import DocReport, ModelBuilder, fold
from ..readers import _decode
from ..registry import Block, FormatSpec, register
from . import _diyagram as diagram
from ._ortak import MAX_TEXT_FILE_BYTES, FormatError, Leftovers, fail, html_to_text, read_limited

_HEADER = re.compile(r"^(?:flowchart|graph)(?:\s+(?:TB|TD|BT|RL|LR))?\s*;?\s*$", re.IGNORECASE)
_HEADER_WITH_DIRECTION = re.compile(r"^(?:flowchart|graph)\s+(?:TB|TD|BT|RL|LR)\s*;?\s*$", re.IGNORECASE)
_OTHER = re.compile(r"^(?:sequenceDiagram|classDiagram|stateDiagram|erDiagram|journey|gantt|pie|gitGraph|mindmap|"
                    r"timeline|quadrantChart|requirementDiagram|C4\w+|sankey|xychart|block|architecture|packet|"
                    r"kanban|zenuml|treemap|radar)", re.IGNORECASE)
_KEYWORDS = ("subgraph", "end", "direction", "classdef", "style", "linkstyle", "click", "acctitle", "accdescr")
_ID = re.compile(r"[\w$#]+(?:[.\-][\w$#]+)*")
#: (açılış, kapanışlar, ipucu) — uzun açılışlar önce denenir
_SHAPES: tuple[tuple[str, tuple[str, ...], str | None], ...] = (
    ("(((", (")))",), None), ("([", ("])",), None), ("[[", ("]]",), diagram.PROCESS),
    ("[(", (")]",), diagram.DATASET), ("((", ("))",), None), ("{{", ("}}",), None),
    ("[/", ("/]", "\\]"), None), ("[\\", ("\\]", "/]"), None), ("[", ("]",), None), ("(", (")",), None),
    ("{", ("}",), None), (">", ("]",), None),
)
_DATA_SHAPES = {"cyl", "cylinder", "das", "db", "database", "lin-cyl", "h-cyl", "horizontal-cylinder", "doc",
                "document", "docs", "documents", "lin-doc", "tag-doc", "st-doc", "bow-rect", "stored-data"}
_PROCESS_SHAPES = {"subproc", "subprocess", "subroutine", "fr-rect", "framed-rectangle", "div-rect", "proc"}
_CLASS_HINTS = {"surec": diagram.PROCESS, "process": diagram.PROCESS, "job": diagram.PROCESS,
                "is": diagram.PROCESS, "gorev": diagram.PROCESS, "task": diagram.PROCESS, "etl": diagram.PROCESS,
                "veri": diagram.DATASET, "dataset": diagram.DATASET, "tablo": diagram.DATASET,
                "table": diagram.DATASET, "dosya": diagram.DATASET, "file": diagram.DATASET,
                "db": diagram.DATASET, "data": diagram.DATASET}
_TEXT_LINK = re.compile(r"(?P<open>--|==|-\.)\s+(?P<label>[^|\n]*?)\s*"
                        r"(?P<close>-{2,}>|-{3,}|={2,}>|={3,}|\.-+>|\.-+)")
_LINK = re.compile(r"(?P<left>[<xo])?(?P<body>-{2,}|={2,}|-\.+-|~{3,})(?P<right>>|[xo](?=[\s|])|)"
                   r"(?:\s*\|(?P<label>[^|]*)\|)?")


@dataclass
class _Node:
    id: str
    label: str | None = None
    hint: str | None = None
    classes: list[str] = field(default_factory=list)


class _Scanner:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def space(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1

    def node(self) -> _Node | None:
        self.space()
        match = _ID.match(self.text, self.pos)
        if not match:
            return None
        self.pos = match.end()
        node = _Node(match.group(0))
        if self.text.startswith("@{", self.pos):
            end = self.text.find("}", self.pos)
            if end < 0:
                raise FormatError("kapanmamış @{ … }")
            node.label, node.hint = _shape_object(self.text[self.pos + 2:end])
            self.pos = end + 1
        else:
            for opener, closers, hint in _SHAPES:
                if self.text.startswith(opener, self.pos):
                    node.label = self._shape_text(opener, closers)
                    node.hint = hint
                    break
        if self.text.startswith(":::", self.pos):
            match = re.compile(r"[\w-]+").match(self.text, self.pos + 3)
            if match:
                node.classes.append(match.group(0))
                self.pos = match.end()
        return node

    def _shape_text(self, opener: str, closers: tuple[str, ...]) -> str:
        start = self.pos + len(opener)
        stripped = self.text[start:].lstrip()
        if stripped.startswith('"'):
            quote_start = len(self.text) - len(stripped)
            quote_end = self.text.find('"', quote_start + 1)
            if quote_end < 0:
                raise FormatError("kapanmamış tırnak")
            label = self.text[quote_start + 1:quote_end]
            rest = quote_end + 1
            while rest < len(self.text) and self.text[rest].isspace():
                rest += 1
            for closer in closers:
                if self.text.startswith(closer, rest):
                    self.pos = rest + len(closer)
                    return _clean(label)
            raise FormatError(f"şekil kapanmamış: {opener}")
        ends = [(self.text.find(closer, start), closer) for closer in closers]
        ends = [(index, closer) for index, closer in ends if index >= 0]
        if not ends:
            raise FormatError(f"şekil kapanmamış: {opener}")
        index, closer = min(ends)
        self.pos = index + len(closer)
        return _clean(self.text[start:index])

    def group(self) -> list[_Node]:
        nodes = []
        node = self.node()
        while node is not None:
            nodes.append(node)
            self.space()
            if not self.text.startswith("&", self.pos):
                break
            self.pos += 1
            node = self.node()
        return nodes

    def link(self) -> tuple[bool, str] | None:
        """(görünür mü, etiket) ya da bağlantı yoksa None."""
        self.space()
        match = _TEXT_LINK.match(self.text, self.pos)
        if match:
            self.pos = match.end()
            return True, _clean(match.group("label"))
        match = _LINK.match(self.text, self.pos)
        if not match:
            return None
        self.pos = match.end()
        return not match.group("body").startswith("~"), _clean(match.group("label") or "")


def _clean(label: str) -> str:
    value = label.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"`":
        value = value[1:-1]
    return html_to_text(value.replace("`", ""))


def _shape_object(body: str) -> tuple[str | None, str | None]:
    """``@{ shape: cyl, label: "DB" }`` → (etiket, ipucu)."""
    pairs = {key.lower(): (quoted or bare or "").strip()
             for key, quoted, bare in re.findall(r'(\w+)\s*:\s*(?:"([^"]*)"|([^,}]+))', body)}
    shape = pairs.get("shape", "").lower()
    hint = diagram.DATASET if shape in _DATA_SHAPES else diagram.PROCESS if shape in _PROCESS_SHAPES else None
    label = pairs.get("label")
    return (_clean(label) if label else None), hint


def _split(line: str) -> list[str]:
    """``;`` ile ayır (tırnak ve parantez içindekiler hariç)."""
    parts, buffer, depth, quote = [], [], 0, False
    for char in line:
        if char == '"':
            quote = not quote
        elif not quote and char in "[({":
            depth += 1
        elif not quote and char in "])}" and depth:
            depth -= 1
        elif char == ";" and not quote and depth == 0:
            parts.append("".join(buffer))
            buffer = []
            continue
        buffer.append(char)
    parts.append("".join(buffer))
    return [part.strip() for part in parts if part.strip()]


def _statements(text: str) -> list[str]:
    lines = text.splitlines()
    start = 0
    if lines and lines[0].strip() == "---":  # YAML ön bilgisi
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                start = index + 1
                break
    out = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped and not stripped.startswith("%%"):
            out.extend(_split(stripped))
    return out


def _hint_from_classes(classes: list[str]) -> str | None:
    for name in classes:
        hint = _CLASS_HINTS.get(fold(name))
        if hint:
            return hint
    return None


@dataclass
class MermaidGraph:
    page: diagram.Page
    unparsed: list[str]


def parse_mermaid(text: str) -> MermaidGraph:
    statements = _statements(text)
    if not statements:
        raise FormatError("dosya boş")
    header = statements[0]
    if _OTHER.match(header):
        raise FormatError(f"yalnız flowchart/graph desteklenir ('{header.split()[0]}' değil)")
    if not _HEADER.match(header):
        raise FormatError("Mermaid akış şeması 'flowchart' ya da 'graph' ile başlamalı")
    nodes: dict[str, _Node] = {}
    links: list[diagram.Link] = []
    unparsed: list[str] = []
    for statement in statements[1:]:
        keyword = statement.split(None, 1)[0].lower()
        if keyword == "class":
            parts = statement.split()
            if len(parts) >= 3:
                for node_id in parts[1].split(","):
                    nodes.setdefault(node_id, _Node(node_id)).classes.append(parts[2])
            continue
        if keyword in _KEYWORDS or keyword.startswith(("acctitle", "accdescr")):
            continue
        scanner = _Scanner(statement)
        try:
            groups = [scanner.group()]
            if not groups[0]:
                raise FormatError("düğüm bekleniyordu")
            visible: list[tuple[bool, str]] = []
            while (link := scanner.link()) is not None:
                following = scanner.group()
                if not following:
                    raise FormatError("bağlantıdan sonra düğüm bekleniyordu")
                visible.append(link)
                groups.append(following)
            scanner.space()
            if scanner.pos != len(statement):
                raise FormatError("fazla karakter")
        except FormatError:
            unparsed.append(statement)
            continue
        for group in groups:
            for node in group:
                known = nodes.setdefault(node.id, _Node(node.id))
                known.label = node.label if node.label is not None else known.label
                known.hint = node.hint or known.hint
                known.classes.extend(node.classes)
        for (shown, label), first, second in zip(visible, groups, groups[1:], strict=False):
            if shown:
                links.extend(diagram.Link(a.id, b.id, label) for a in first for b in second)
    page = diagram.Page()
    for node in nodes.values():
        page.node(node.id, node.label if node.label is not None else node.id,
                  node.hint or _hint_from_classes(node.classes))
    page.links = links
    return MermaidGraph(page, unparsed)


def _load(path: Path) -> MermaidGraph:
    return parse_mermaid(_decode(read_limited(path, MAX_TEXT_FILE_BYTES)))


def looks_like_mermaid(head: bytes, name: str) -> bool:
    statements = _statements(_decode(head[:8192]))
    return bool(statements) and bool(_HEADER_WITH_DIRECTION.match(statements[0]))


def read_mermaid(builder: ModelBuilder, report: DocReport, path: Path) -> Leftovers:
    try:
        graph = _load(path)
    except FormatError as exc:
        if "yalnız flowchart" in str(exc):
            report.status = "unsupported"
            report.note(f"Mermaid: {exc}")
            return []
        return fail(report, f"Mermaid okunamadı: {exc}")
    if graph.unparsed:
        report.note(f"{len(graph.unparsed)} ifade ayrıştırılamadı (ör. {graph.unparsed[0][:80]!r})")
    return diagram.apply(builder, report, path.name, [graph.page], extractor="diyagram:mermaid")


def preview_mermaid(path: Path) -> list[Block]:
    return diagram.preview([_load(path).page])


register(FormatSpec("mermaid", "Mermaid", (".mmd", ".mermaid"), read_mermaid, preview_mermaid,
                    sniff=looks_like_mermaid, group="Diyagram",
                    description="flowchart/graph: düğümler → tablo/job, oklar → akış, ok etiketi → job",
                    sample="ornek_bicimler/kart_akis.mmd"))

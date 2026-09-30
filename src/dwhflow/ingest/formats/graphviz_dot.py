"""Graphviz DOT (``.dot``, ``.gv``; ``digraph … {`` ile başlayan ``.txt``).

Tam DOT dilbilgisi: ``strict``, ``graph``/``digraph``, düğüm/kenar/grafik öznitelikleri ve
varsayılanları (``node [shape=cylinder]``, kapsamlı), ``subgraph``/``{ … }`` (kenar ucu da
olabilir: ``{A B} -> C``), zincirler (``A -> B -> C [label="JOB"]`` her kenara uygulanır),
bağlantı noktaları (``A:p1``), tırnaklı ve HTML (``<…>``) kimlikler, ``+`` ile birleştirilen
dizgeler, ``//``, ``/* */`` ve ``#`` yorumları. Etiketlerde ``\\n``, ``\\l``, ``\\N`` çözülür.

İpuçları: ``shape=cylinder|folder|note|tab`` → veri nesnesi, ``shape=component`` → süreç;
``class``/``tur`` özniteliği ``surec``/``veri`` açık ipucudur. Kenar etiketi bir job adıysa akış
o süreçten geçer (ortak kural: ``_diyagram``). Yönsüz grafikte (``graph``, ``--``) yön yazılış
sırasıdır. Aynı dosyadaki birden çok grafik ``grafik n`` önekiyle ayrılır.

``.dot`` aynı zamanda Word 97-2003 şablon uzantısıdır: OLE2 imzalı dosyalar Word okuyucusuna gider.
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
from .eski_ofis import OLE_SIGNATURE, preview_doc, read_doc

_TOKEN = re.compile(
    r"(?P<space>\s+)"
    r"|(?P<comment>//[^\n]*|/\*.*?\*/|(?<![^\n])[ \t]*\#[^\n]*)"
    r"|(?P<edge>->|--)"
    r"|(?P<punct>[{}\[\];,=:+])"
    r'|(?P<string>"(?:\\.|[^"\\])*")'
    r"|(?P<html><)"
    r"|(?P<id>-?(?:\.\d+|\d+(?:\.\d*)?)|[A-Za-z_\u0080-\U0010ffff][\w\u0080-\U0010ffff]*)",
    re.DOTALL,
)
_KEYWORDS = {"node", "edge", "graph", "digraph", "subgraph", "strict"}
_DATA_SHAPES = {"cylinder", "folder", "note", "tab"}
_PROCESS_SHAPES = {"component"}
_CLASS_HINTS = {"surec": diagram.PROCESS, "process": diagram.PROCESS, "job": diagram.PROCESS,
                "veri": diagram.DATASET, "dataset": diagram.DATASET, "tablo": diagram.DATASET,
                "table": diagram.DATASET, "dosya": diagram.DATASET}
_START = re.compile(rb"^\s*(?:(?://|#)[^\n]*\n\s*|/\*.*?\*/\s*)*(?:strict\s+)?(?:di)?graph\b\s*"
                    rb"(?:\"[^\"]*\"|[\w.]+)?\s*\{", re.IGNORECASE | re.DOTALL)


@dataclass
class _Token:
    kind: str
    value: str
    line: int


@dataclass
class DotGraph:
    directed: bool
    name: str = ""
    nodes: dict[str, dict[str, str]] = field(default_factory=dict)
    edges: list[tuple[str, str, str]] = field(default_factory=list)


def _html_end(text: str, start: int) -> int:
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "<":
            depth += 1
        elif text[index] == ">":
            depth -= 1
            if depth == 0:
                return index
    raise FormatError("kapanmamış HTML etiketi (<…>)")


def tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    position, line = 0, 1
    while position < len(text):
        match = _TOKEN.match(text, position)
        if match is None:
            raise FormatError(f"satır {line}: beklenmeyen karakter {text[position]!r}")
        kind = match.lastgroup or ""
        if kind == "html":
            end = _html_end(text, position)
            tokens.append(_Token("html", text[position + 1:end], line))
            line += text.count("\n", position, end + 1)
            position = end + 1
            continue
        value = match.group(0)
        if kind == "string":
            tokens.append(_Token("id", value[1:-1].replace('\\"', '"').replace("\\\n", ""), line))
        elif kind not in ("space", "comment"):
            tokens.append(_Token(kind, value, line))
        line += value.count("\n")
        position = match.end()
    return tokens


class _Parser:
    def __init__(self, tokens: list[_Token]) -> None:
        self.tokens = tokens
        self.index = 0

    def peek(self, offset: int = 0) -> _Token | None:
        index = self.index + offset
        return self.tokens[index] if index < len(self.tokens) else None

    def is_punct(self, value: str, offset: int = 0) -> bool:
        token = self.peek(offset)
        return token is not None and token.kind == "punct" and token.value == value

    def is_keyword(self, value: str) -> bool:
        token = self.peek()
        return token is not None and token.kind == "id" and token.value.lower() == value

    def take(self) -> _Token:
        token = self.peek()
        if token is None:
            raise FormatError("dosya beklenmedik biçimde bitti")
        self.index += 1
        return token

    def expect(self, value: str) -> None:
        token = self.take()
        if token.kind != "punct" or token.value != value:
            raise FormatError(f"satır {token.line}: '{value}' bekleniyordu, '{token.value}' bulundu")

    def ident(self) -> str:
        token = self.take()
        if token.kind == "html":
            return html_to_text(token.value)
        if token.kind != "id":
            raise FormatError(f"satır {token.line}: ad bekleniyordu, '{token.value}' bulundu")
        value = token.value
        while self.is_punct("+") and self.peek(1) is not None and self.peek(1).kind == "id":  # "a" + "b"
            self.index += 1
            value += self.take().value
        return value

    # -- dilbilgisi ---------------------------------------------------------- #

    def graphs(self) -> list[DotGraph]:
        out = []
        while self.peek() is not None:
            out.append(self.graph())
        return out

    def graph(self) -> DotGraph:
        if self.is_keyword("strict"):
            self.take()
        token = self.take()
        if token.kind != "id" or token.value.lower() not in ("graph", "digraph"):
            raise FormatError(f"satır {token.line}: 'graph' ya da 'digraph' bekleniyordu")
        graph = DotGraph(directed=token.value.lower() == "digraph")
        if not self.is_punct("{"):
            graph.name = self.ident()
        self.expect("{")
        self.statements(graph, {"node": {}, "edge": {}})
        self.expect("}")
        return graph

    def statements(self, graph: DotGraph, scope: dict[str, dict[str, str]]) -> set[str]:
        mentioned: set[str] = set()
        while not self.is_punct("}"):
            if self.peek() is None:
                raise FormatError("kapanmamış '{'")
            mentioned |= self.statement(graph, scope)
            while self.is_punct(";") or self.is_punct(","):
                self.take()
        return mentioned

    def statement(self, graph: DotGraph, scope: dict[str, dict[str, str]]) -> set[str]:
        token = self.peek()
        assert token is not None
        word = token.value.lower() if token.kind == "id" else ""
        if word in ("node", "edge", "graph") and self.is_punct("[", 1):
            self.take()
            attrs = self.attributes()
            if word != "graph":
                scope[word].update(attrs)
            return set()
        if token.kind in ("id", "html") and self.is_punct("=", 1):  # rankdir=LR
            self.ident()
            self.take()
            self.ident()
            return set()
        chain = [self.endpoint(graph, scope)]
        while (edge := self.peek()) is not None and edge.kind == "edge":
            self.take()
            chain.append(self.endpoint(graph, scope))
        attrs = self.attributes() if self.is_punct("[") else {}
        if len(chain) == 1:
            ids, single = chain[0]
            if single:
                graph.nodes[next(iter(ids))].update(attrs)
            return ids
        label = {**scope["edge"], **attrs}.get("label", "")
        for (sources, _), (targets, _) in zip(chain, chain[1:], strict=False):
            graph.edges.extend((a, b, label) for a in sorted(sources) for b in sorted(targets))
        return set().union(*(ids for ids, _ in chain))

    def endpoint(self, graph: DotGraph, scope: dict[str, dict[str, str]]) -> tuple[set[str], bool]:
        """(düğüm kümesi, tek düğüm mü)."""
        if self.is_keyword("subgraph") or self.is_punct("{"):
            if self.is_keyword("subgraph"):
                self.take()
                if not self.is_punct("{"):
                    self.ident()
            self.expect("{")
            inner = {"node": dict(scope["node"]), "edge": dict(scope["edge"])}
            ids = self.statements(graph, inner)
            self.expect("}")
            return ids, False
        node_id = self.ident()
        if self.is_punct(":"):  # bağlantı noktası (port) ve pusula
            self.take()
            self.ident()
            if self.is_punct(":"):
                self.take()
                self.ident()
        if node_id not in graph.nodes:
            graph.nodes[node_id] = dict(scope["node"])
        return {node_id}, True

    def attributes(self) -> dict[str, str]:
        attrs: dict[str, str] = {}
        while self.is_punct("["):
            self.take()
            while not self.is_punct("]"):
                key = self.ident().lower()
                value = "true"
                if self.is_punct("="):
                    self.take()
                    value = self.ident()
                attrs[key] = value
                while self.is_punct(",") or self.is_punct(";"):
                    self.take()
            self.expect("]")
        return attrs


def _label(raw: str, node_id: str, shape: str) -> str:
    value = raw.replace("\\N", node_id).replace("\\G", "")
    value = re.sub(r"\\[nlr]", "\n", value).replace("\\\\", "\\")
    if shape in ("record", "mrecord"):
        value = re.sub(r"<[^>]*>", "", value).replace("|", "\n").replace("{", "").replace("}", "")
    return value


def _hint(attrs: dict[str, str]) -> str | None:
    for key in ("tur", "class"):
        for word in (attrs.get(key) or "").split():
            hint = _CLASS_HINTS.get(fold(word))
            if hint:
                return hint
    shape = (attrs.get("shape") or "").lower()
    if shape in _DATA_SHAPES:
        return diagram.DATASET
    if shape in _PROCESS_SHAPES:
        return diagram.PROCESS
    return None


def dot_pages(text: str) -> tuple[list[diagram.Page], bool]:
    """(sayfalar, yönsüz grafik var mı)."""
    graphs = _Parser(tokenize(text)).graphs()
    if not graphs:
        raise FormatError("dosyada graph/digraph tanımı yok")
    pages = []
    for number, graph in enumerate(graphs, start=1):
        page = diagram.Page(prefix=f"grafik {number}" if len(graphs) > 1 else "", title=graph.name)
        for node_id, attrs in graph.nodes.items():
            shape = (attrs.get("shape") or "").lower()
            page.node(node_id, _label(attrs.get("label") or node_id, node_id, shape), _hint(attrs))
        page.links = [diagram.Link(a, b, _label(label, "", "")) for a, b, label in graph.edges]
        pages.append(page)
    return pages, any(not graph.directed for graph in graphs)


def looks_like_dot(head: bytes, name: str) -> bool:
    return bool(_START.match(head[:8192].lstrip(b"\xef\xbb\xbf")))


def read_dot(builder: ModelBuilder, report: DocReport, path: Path) -> Leftovers:
    try:
        data = read_limited(path, MAX_TEXT_FILE_BYTES)
        if data.startswith(OLE_SIGNATURE):
            report.note("dosya bir Word 97-2003 şablonu (.dot); Word okuyucusuyla okundu")
            return read_doc(builder, report, path)
        pages, undirected = dot_pages(_decode(data))
    except FormatError as exc:
        return fail(report, f"DOT okunamadı: {exc}")
    if undirected:
        report.note("yönsüz grafik: bağlantılar yazıldığı sırayla (soldan sağa) yorumlandı")
    return diagram.apply(builder, report, path.name, pages, extractor="diyagram:dot")


def preview_dot(path: Path) -> list[Block]:
    data = read_limited(path, MAX_TEXT_FILE_BYTES)
    if data.startswith(OLE_SIGNATURE):
        return preview_doc(path)
    return diagram.preview(dot_pages(_decode(data))[0])


register(FormatSpec("dot", "Graphviz DOT", (".dot", ".gv"), read_dot, preview_dot, sniff=looks_like_dot,
                    group="Diyagram", description="digraph: düğümler → tablo/job, kenarlar → akış, etiket → job",
                    sample="ornek_bicimler/kart_akis.dot"))

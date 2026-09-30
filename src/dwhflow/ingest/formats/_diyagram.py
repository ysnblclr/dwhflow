"""Diyagram → soy (lineage): Visio, draw.io, BPMN, Mermaid ve Graphviz okuyucularının ortak kuralı.

**Düğüm sınıflandırması** (her diyagram biçiminde aynı; ilk tutan kural kazanır):

1. Açık ipucu: şekil/kalıp türü (silindir, veri deposu, belge → veri nesnesi; işlem/alt
   süreç → süreç), BPMN öğe türü, Mermaid/DOT sınıfı ya da ``tur`` özniteliği
   (``surec``/``veri``).
2. Ad kuralları (``explain_name``, ayarlardaki kurallar dahil) bir veri katmanı tanıyorsa →
   veri nesnesi.
3. Ad bir job'a benziyorsa → süreç: ``JOB_ SP_ PX_ TPT_ EXT_ LOAD_ ETL_ SEQ_ MFT_ BTEQ_ FTP_
   SFTP_ PRC_ PROC_ WF_ WKF_ BOX_ CMD_ INF_`` önekleri ya da "job", "prosedür", "procedure",
   "yükle", "load", "script", "betik", "etl", "görev", "task" sözcükleri.
4. Aksi hâlde veri nesnesi.

**Bağlantılar** (yön: başlangıç → bitiş): veri→veri = türetme; bağlantı etiketi bir süreç
adıysa akış o süreç üzerinden kurulur (okur + yazar). Etiket tek bir tanımlayıcıysa
(``LOAD_F_KART_ISLEM``) ya da içinde job'a benzeyen tek bir ad varsa süreç sayılır; serbest
metin etiketler ("günlük") süreç yapılmaz, yalnız kanıta yazılır. veri→süreç = okur;
süreç→veri = yazar; süreç→süreç = önce gelir.

**Ad:** şekil metninin ilk satırı; satırda birden çok sözcük ve tek bir tablo/job adı varsa
("Kaynak: SRC_KART_ISLEM") o ad alınır, metnin tamamı açıklama olur. Başla/Bitir gibi akış
işaretleri ve boş şekiller atlanır. Hiç bağlantısı olmayan şekil yalnız adı tablo/job'a
benziyorsa modele girer; değilse (açıklama kutusu, başlık) metni metin kurallarından geçer.

**Konum:** ``sayfa 1 · şekil STG_KART_ISLEM``, ``sayfa 1 · ok STG_KART_ISLEM → ODS_KART_ISLEM``,
serbest metin kutuları ``sayfa 1 · metin 2``. Sayfası olmayan biçimlerde önek yoktur.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..base import DocReport, ModelBuilder, bare_key, explain_name, fold, name_key, ref
from ..preview import MAX_BLOCKS
from ..registry import Block
from ..text import _IDENT, extract_paragraphs, looks_like_table
from ._ortak import Leftovers, one_line, squeeze

CONFIDENCE = 0.8

_JOB_PREFIXES = ("JOB_", "SP_", "PX_", "TPT_", "EXT_", "LOAD_", "ETL_", "SEQ_", "MFT_", "BTEQ_", "FTP_",
                 "SFTP_", "PRC_", "PROC_", "WF_", "WKF_", "BOX_", "CMD_", "INF_")
_JOB_WORDS = ("job", "prosedur", "procedure", "yukle", "load", "script", "betik", "etl", "gorev", "task")
_STOP_LABELS = {"start", "end", "stop", "begin", "basla", "baslangic", "bitis", "bitir", "son", "sonu"}

DATASET, PROCESS, TEXT, SKIP = "dataset", "process", "text", "skip"


@dataclass
class Node:
    id: str
    label: str
    hint: str | None = None  # dataset | process | text | skip | None


@dataclass
class Link:
    source: str
    target: str
    label: str = ""


@dataclass
class Page:
    prefix: str = ""  # "sayfa 1" ya da ""
    title: str = ""
    nodes: dict[str, Node] = field(default_factory=dict)
    links: list[Link] = field(default_factory=list)

    def node(self, node_id: str, label: str, hint: str | None = None) -> Node:
        existing = self.nodes.get(node_id)
        if existing is None:
            existing = self.nodes[node_id] = Node(node_id, label, hint)
        else:
            existing.label = label or existing.label
            existing.hint = hint or existing.hint
        return existing

    def where(self, text: str) -> str:
        return f"{self.prefix} · {text}" if self.prefix else text


# --------------------------------------------------------------------------- #
# Sınıflandırma
# --------------------------------------------------------------------------- #


def looks_like_job(name: str) -> bool:
    bare = bare_key(name_key(name))
    if bare.startswith(_JOB_PREFIXES):
        return True
    return any(word.startswith(_JOB_WORDS) for word in fold(name).split())


def classify(name: str, hint: str | None) -> str:
    if hint in (DATASET, PROCESS):
        return hint
    if explain_name(name)["layer"] is not None:
        return DATASET
    return PROCESS if looks_like_job(name) else DATASET


def _identifiers(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(1) for m in _IDENT.finditer(text) if looks_like_table(m.group(1))))


def node_name(label: str) -> tuple[str, str | None] | None:
    """(ad, açıklama) ya da boş şekil için None."""
    text = squeeze(label)
    if not text:
        return None
    lines = text.split("\n")
    first = one_line(lines[0])
    if " " in first:
        names = _identifiers(first)
        if len(names) == 1:
            return names[0], one_line(text)
    description = one_line(" ".join(lines[1:])) or None
    return first[:128], description


def label_process(label: str) -> str | None:
    """Bağlantı etiketi süreç adı mı? (tek tanımlayıcı ya da içinde tek bir job adı)"""
    text = one_line(squeeze(label))
    if not text:
        return None
    names = _identifiers(text)
    if len(names) == 1 and names[0] == text:
        return text
    jobs = [name for name in names if looks_like_job(name)]
    return jobs[0] if len(jobs) == 1 else None


# --------------------------------------------------------------------------- #
# Plan: okuyucu ve önizleme aynı kararları ve konumları kullanır
# --------------------------------------------------------------------------- #


@dataclass
class _Decision:
    kind: str  # dataset | process | text
    name: str
    description: str | None
    locator: str
    evidence: str


@dataclass
class _PageLink:
    source: _Decision
    target: _Decision
    via: str | None
    locator: str
    evidence: str


def _plan(page: Page) -> tuple[list[_Decision], list[_PageLink]]:
    connected = {link.source for link in page.links} | {link.target for link in page.links}
    decided: dict[str, _Decision] = {}
    order: list[_Decision] = []
    texts = 0
    for node in page.nodes.values():
        if node.hint == SKIP:
            continue
        if node.hint == TEXT:
            text = squeeze(node.label)
            if text:
                texts += 1
                order.append(_Decision(TEXT, "", None, page.where(f"metin {texts}"), text))
            continue
        named = node_name(node.label)
        if named is None:
            continue
        name, description = named
        if fold(name) in _STOP_LABELS:
            continue
        locator = page.where(f"şekil {name}")
        if node.id not in connected and node.hint is None and not (
                looks_like_table(name) or looks_like_job(name) or explain_name(name)["layer"] is not None):
            order.append(_Decision(TEXT, name, None, locator, squeeze(node.label)))
            continue
        decision = _Decision(classify(name, node.hint), name, description, locator, one_line(node.label))
        decided[node.id] = decision
        order.append(decision)
    links: list[_PageLink] = []
    for link in page.links:
        source, target = decided.get(link.source), decided.get(link.target)
        if source is None or target is None or source.name == target.name:
            continue
        via = label_process(link.label) if source.kind == target.kind == DATASET else None
        label = one_line(squeeze(link.label))
        evidence = f"{source.name} → {target.name}" + (f" ({label})" if label else "")
        links.append(_PageLink(source, target, via, page.where(f"ok {source.name} → {target.name}"), evidence))
    return order, links


def apply(builder: ModelBuilder, report: DocReport, file: str, pages: list[Page], *,
          extractor: str, confidence: float = CONFIDENCE) -> Leftovers:
    """Sayfaları modele işler; serbest metinleri metin kurallarından geçirip artanları döner."""
    paragraphs: list[tuple[str, str]] = []
    shapes = 0
    for page in pages:
        order, links = _plan(page)
        keys: dict[int, str] = {}
        for decision in order:
            if decision.kind == TEXT:
                paragraphs.append((decision.locator, decision.evidence))
                continue
            shapes += 1
            source = ref(file, decision.locator, extractor, confidence=confidence, evidence=decision.evidence)
            if decision.kind == DATASET:
                keys[id(decision)] = builder.dataset(decision.name, source, description=decision.description)
            else:
                keys[id(decision)] = builder.process(decision.name, source, description=decision.description)
        for link in links:
            source = ref(file, link.locator, extractor, confidence=confidence, evidence=link.evidence)
            first, second = keys[id(link.source)], keys[id(link.target)]
            kinds = (link.source.kind, link.target.kind)
            if kinds == (DATASET, DATASET):
                via = builder.process(link.via, source) if link.via else None
                if builder.flow(first, second, via=via, source=source):
                    report.flows += 1
            elif kinds == (DATASET, PROCESS):
                builder.reads(first, second, source)
                report.flows += 1
            elif kinds == (PROCESS, DATASET):
                builder.writes(first, second, source)
                report.flows += 1
            else:
                builder.precedes(first, second, source)
                report.flows += 1
    if not shapes and not paragraphs:
        report.note("diyagramda metinli şekil bulunamadı")
    result = extract_paragraphs(paragraphs, builder, report, file=file)
    report.flows += result.flows
    return result.leftovers


_KIND_LABELS = {DATASET: "veri nesnesi", PROCESS: "süreç"}


def preview(pages: list[Page]) -> list[Block]:
    blocks: list[Block] = []
    for page in pages:
        order, links = _plan(page)
        if page.prefix:
            blocks.append({"locator": page.prefix, "kind": "heading",
                           "text": f"{page.prefix} — {page.title}" if page.title else page.prefix})
        for decision in order:
            if decision.kind == TEXT:
                text = decision.evidence
            else:
                text = f"{decision.name} — {_KIND_LABELS[decision.kind]}"
                if decision.description:
                    text += f" ({decision.description})"
            blocks.append({"locator": decision.locator, "kind": "paragraph", "text": text})
        for link in links:
            text = link.evidence + (f" — süreç: {link.via}" if link.via else "")
            blocks.append({"locator": link.locator, "kind": "paragraph", "text": text})
        if len(blocks) >= MAX_BLOCKS:
            break
    if not blocks:
        blocks.append({"locator": "", "kind": "note", "text": "Diyagramda metinli şekil bulunamadı."})
    return blocks[:MAX_BLOCKS]

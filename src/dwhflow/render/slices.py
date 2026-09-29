"""Dilimleyici: modelden hangi diyagramların üretileceğine karar verir.

Archify küratörlü, okunabilir diyagramlar için tasarlanmış (ör. dataflow en
fazla 5 aşama; masaüstünde yazılar en az 6 px). Tüm ambar tek diyagrama
sığmaz; bu yüzden model **dilimlenir**: genel akış (sığıyorsa), her tüketim
ucunun soy ağacı, istenirse A→B yolu ya da bir nesnenin çevresi.

Diyagram görünümünde süreçler (job'lar) kenar etiketine dönüşür; node'lar veri
nesneleridir (tablo, dosya, rapor). Bu yüzden dilimleme **veri nesnesi grafı**
üzerinde yapılır: iki tablo, aralarında onları bağlayan bir job varsa komşudur.
"""

from __future__ import annotations

import itertools
import re
from collections import deque
from dataclasses import dataclass, field

import networkx as nx

from ..model import (
    DatasetKind,
    EdgeKind,
    Layer,
    Model,
    ProcessKind,
    Status,
)

#: Tek diyagramda gösterilecek en fazla veri nesnesi. Ölçüm: 20–25 nesnede
#: showcase kapıları hâlâ geçiyor; daha fazlası okunabilirliği bozuyor.
DATASET_BUDGET = 24
#: Otomatik planda üretilecek en fazla soy diyagramı.
MAX_LINEAGE_SLICES = 12

_TERMINAL_KINDS = {DatasetKind.REPORT, DatasetKind.DOWNSTREAM_SYSTEM, DatasetKind.EXPORT_FILE}


@dataclass
class Link:
    """İki veri nesnesi arasındaki, süreç(ler) üzerinden kurulan bağ."""

    source: str
    target: str
    processes: list[str] = field(default_factory=list)
    direct_kinds: list[str] = field(default_factory=list)
    #: En az bir kesin (taslak olmayan) kaynak var mı? Bağ, ancak TÜM katkıları
    #: taslaksa taslaktır: SQL'de de geçen bir akışı metinde de anlatmak onu taslak yapmaz.
    confirmed: bool = False

    @property
    def draft(self) -> bool:
        return not self.confirmed

    @property
    def key(self) -> tuple[str, str]:
        return (self.source, self.target)


@dataclass
class Slice:
    id: str
    title: str
    datasets: list[str]
    kind: str = "lineage"  # overview | lineage | path | focus | batch
    subtitle: str = ""
    processes: list[str] = field(default_factory=list)  # yalnız batch görünümünde
    highlight: list[str] = field(default_factory=list)
    focus: str | None = None
    trimmed: bool = False


# --------------------------------------------------------------------------- #
# Veri nesnesi grafı
# --------------------------------------------------------------------------- #


def dataset_links(model: Model) -> dict[tuple[str, str], Link]:
    """Süreçleri kenara indirerek tablo→tablo bağlarını çıkarır."""
    inputs: dict[str, set[str]] = {}
    outputs: dict[str, set[str]] = {}
    confirmed_edges: set[tuple[str, str]] = set()
    links: dict[tuple[str, str], Link] = {}

    for edge in model.edges:
        source, target = edge.source_node, edge.target_node
        if edge.status is not Status.DRAFT:
            confirmed_edges.add((source, target))
        if edge.kind in (EdgeKind.READS, EdgeKind.CONSUMES_FILE):
            if source in model.datasets and target in model.processes:
                inputs.setdefault(target, set()).add(source)
        elif edge.kind in (EdgeKind.WRITES, EdgeKind.PRODUCES_FILE):
            if source in model.processes and target in model.datasets:
                outputs.setdefault(source, set()).add(target)
        elif edge.kind in (EdgeKind.DERIVES_FROM, EdgeKind.TRANSFERS):
            if source in model.datasets and target in model.datasets and source != target:
                link = links.setdefault((source, target), Link(source, target))
                link.direct_kinds.append(edge.kind.value)
                link.confirmed = link.confirmed or edge.status is not Status.DRAFT

    for process_id in sorted(set(inputs) | set(outputs)):
        process = model.processes[process_id]
        if process.kind in (ProcessKind.DQ_CHECK, ProcessKind.RECONCILIATION):
            continue  # kontrol süreçleri veri taşımaz
        for source in sorted(inputs.get(process_id, ())):
            for target in sorted(outputs.get(process_id, ())):
                if source == target:
                    continue
                link = links.setdefault((source, target), Link(source, target))
                link.processes.append(process_id)
                if (source, process_id) in confirmed_edges and (process_id, target) in confirmed_edges:
                    link.confirmed = True
    return links


def dataset_graph(model: Model, links: dict[tuple[str, str], Link] | None = None) -> nx.DiGraph:
    graph = nx.DiGraph()
    graph.add_nodes_from(model.datasets)
    for (source, target) in (links or dataset_links(model)):
        graph.add_edge(source, target)
    return graph


# --------------------------------------------------------------------------- #
# Yardımcılar
# --------------------------------------------------------------------------- #


def slug(text: str) -> str:
    table = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    value = re.sub(r"[^a-z0-9]+", "-", text.translate(table).lower()).strip("-")
    return value[:48] or "diyagram"


def resolve(model: Model, ref: str) -> str:
    """Kullanıcının verdiği id ya da adı veri nesnesi id'sine çevirir."""
    if ref in model.datasets:
        return ref
    needle = ref.strip().casefold()
    matches = [d.id for d in model.datasets.values()
               if d.name.casefold() == needle or d.id.casefold() == needle]
    if not matches:
        matches = [d.id for d in model.datasets.values() if needle in d.name.casefold()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise KeyError(f"'{ref}' adında bir tablo/dosya bulunamadı")
    raise KeyError(f"'{ref}' birden fazla nesneyle eşleşiyor: {', '.join(sorted(matches)[:6])}")


def _nearest(graph: nx.DiGraph, center: str, members: set[str], budget: int) -> tuple[list[str], bool]:
    """Bütçeyi aşan kümeyi merkeze en yakın nesnelerle sınırlar (BFS, iki yön)."""
    if len(members) <= budget:
        return sorted(members), False
    undirected = graph.to_undirected(as_view=True)
    order: list[str] = []
    seen = {center}
    queue = deque([center])
    while queue and len(order) < budget:
        node = queue.popleft()
        if node in members:
            order.append(node)
        for neighbor in sorted(undirected.neighbors(node)):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return order, True


# --------------------------------------------------------------------------- #
# Dilim üreticileri
# --------------------------------------------------------------------------- #


def slice_overview(model: Model, budget: int = DATASET_BUDGET) -> Slice | None:
    if not model.datasets or len(model.datasets) > budget:
        return None
    return Slice(
        id="genel-akis",
        title=f"Genel akış — {model.name}",
        datasets=sorted(model.datasets),
        kind="overview",
        subtitle=f"{len(model.datasets)} veri nesnesi, {len(model.processes)} süreç",
    )


def slice_lineage(model: Model, graph: nx.DiGraph, endpoint: str,
                  budget: int = DATASET_BUDGET) -> Slice:
    """Bir tüketim ucunu besleyen her şey (yukarı akış soy ağacı)."""
    members = nx.ancestors(graph, endpoint) | {endpoint}
    chosen, trimmed = _nearest(graph, endpoint, members, budget)
    name = model.datasets[endpoint].name
    subtitle = f"{name} nesnesini besleyen {len(chosen) - 1} veri nesnesi"
    if trimmed:
        subtitle += f" (en yakın {budget}; toplam {len(members)})"
    return Slice(id=f"soy-{slug(endpoint)}", title=f"Soy ağacı: {name}", datasets=chosen,
                 kind="lineage", subtitle=subtitle, focus=endpoint, trimmed=trimmed)


def slice_path(model: Model, graph: nx.DiGraph, source: str, target: str,
               max_paths: int = 3, budget: int = DATASET_BUDGET) -> Slice:
    """A'dan B'ye yollar. En kısa yol vurgulanır; alternatifler bütçe elverdikçe eklenir.

    ``shortest_simple_paths`` bir üretici: tüm yolları hesaplamadan ilk birkaçını
    verir (yoğun JOIN ağlarında yol sayısı katman başına katlanarak büyür).
    """
    source, target = resolve(model, source), resolve(model, target)
    try:
        routes = list(itertools.islice(nx.shortest_simple_paths(graph, source, target), max_paths))
    except nx.NetworkXNoPath:
        raise KeyError(
            f"{model.datasets[source].name} → {model.datasets[target].name} arasında veri akışı yolu yok"
        ) from None
    members: list[str] = []
    for route in routes:
        extra = [n for n in route if n not in members]
        if len(members) + len(extra) > budget and members:
            break
        members.extend(extra)
    highlight = routes[0]
    names = (model.datasets[source].name, model.datasets[target].name)
    return Slice(
        id=f"yol-{slug(source)}-{slug(target)}",
        title=f"Yol: {names[0]} → {names[1]}",
        datasets=members,
        kind="path",
        subtitle=f"en kısa yol {len(highlight) - 1} adım; {len(routes)} yol gösteriliyor",
        highlight=highlight,
    )


def slice_focus(model: Model, graph: nx.DiGraph, center: str, hops: int = 2,
                budget: int = DATASET_BUDGET) -> Slice:
    center = resolve(model, center)
    up = nx.single_source_shortest_path_length(graph.reverse(copy=False), center, cutoff=hops)
    down = nx.single_source_shortest_path_length(graph, center, cutoff=hops)
    chosen, trimmed = _nearest(graph, center, set(up) | set(down), budget)
    name = model.datasets[center].name
    return Slice(id=f"odak-{slug(center)}", title=f"Çevre: {name}", datasets=chosen,
                 kind="focus", subtitle=f"{hops} adım yukarı ve aşağı akış", focus=center,
                 trimmed=trimmed)


#: Batch zinciri diyagramında en fazla job.
PROCESS_BUDGET = 30


def batch_graph(model: Model) -> nx.DiGraph:
    graph = nx.DiGraph()
    for edge in model.edges:
        if edge.kind in (EdgeKind.PRECEDES, EdgeKind.TRIGGERS):
            if edge.source_node in model.processes and edge.target_node in model.processes:
                graph.add_edge(edge.source_node, edge.target_node)
    return graph


def slice_batch(model: Model, budget: int = PROCESS_BUDGET) -> list[Slice]:
    """Job'ların yürütme sırası (scheduler bağımlılıkları). Her bağlı küme bir diyagram."""
    graph = batch_graph(model)
    pieces = []
    components = sorted(nx.weakly_connected_components(graph), key=len, reverse=True)
    for index, component in enumerate(components, start=1):
        if len(component) < 2:
            continue
        members = sorted(component)
        trimmed = len(members) > budget
        if trimmed:
            order = list(nx.topological_sort(graph.subgraph(members))) if nx.is_directed_acyclic_graph(
                graph.subgraph(members)) else members
            members = order[:budget]
        suffix = f" {index}" if len(components) > 1 else ""
        pieces.append(Slice(
            id=f"batch-zinciri{('-' + str(index)) if suffix else ''}",
            title=f"Batch zinciri{suffix}",
            datasets=[], processes=members, kind="batch",
            subtitle=f"{len(members)} job, yürütme sırasına göre" + (" (kırpıldı)" if trimmed else ""),
            trimmed=trimmed,
        ))
    return pieces


def terminal_datasets(model: Model, graph: nx.DiGraph) -> list[str]:
    """Tüketim uçları: rapor/dış sistem/çıkış dosyası ya da kimsenin okumadığı nesneler."""
    ends = []
    for dataset_id, dataset in model.datasets.items():
        if graph.in_degree(dataset_id) == 0:
            continue  # kaynak; soy ağacı boş olur
        if (dataset.kind in _TERMINAL_KINDS or dataset.layer is Layer.CONSUMPTION
                or graph.out_degree(dataset_id) == 0):
            ends.append(dataset_id)
    return sorted(ends, key=lambda d: (-model.datasets[d].criticality, model.datasets[d].name))


def plan_default(model: Model, *, budget: int = DATASET_BUDGET, job_budget: int = PROCESS_BUDGET,
                 overview: bool = True, lineage: bool = True, batch: bool = True) -> list[Slice]:
    """Otomatik plan: genel akış (sığıyorsa) + her tüketim ucunun soy ağacı + batch zincirleri.

    Hangi türlerin üretileceği ve bütçeler proje ayarlarından (``diyagram``) gelir.
    """
    links = dataset_links(model)
    graph = dataset_graph(model, links)
    plan: list[Slice] = []
    general = slice_overview(model, budget) if overview else None
    if general:
        plan.append(general)
    seen = {frozenset(general.datasets)} if general else set()
    for endpoint in terminal_datasets(model, graph) if lineage else []:
        candidate = slice_lineage(model, graph, endpoint, budget=budget)
        key = frozenset(candidate.datasets)
        # Genel akışın aynısı ya da başka bir soy ağacının alt kümesi olan dilimi atla.
        if key in seen or any(key < other for other in seen):
            continue
        seen.add(key)
        plan.append(candidate)
        if len(plan) >= MAX_LINEAGE_SLICES + (1 if general else 0):
            break
    if batch:
        plan.extend(slice_batch(model, budget=job_budget))
    return plan


__all__ = [
    "DATASET_BUDGET",
    "Link",
    "Slice",
    "dataset_graph",
    "dataset_links",
    "plan_default",
    "resolve",
    "slice_batch",
    "slice_focus",
    "slice_lineage",
    "slice_overview",
    "slice_path",
    "slug",
    "terminal_datasets",
]

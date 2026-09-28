"""Graf motoru: yol bulma, komşuluk, etki analizi (PLAN.md Bölüm 4, 8.5).

Bütün kenarlar akış yönünde tutulduğu için (bkz. ``EdgeKind``) "upstream" =
gelen kenarlar, "downstream" = giden kenarlar. Ayrı bir yön çevirme mantığı yok.

Kolon kenarları (``column_maps_to`` / ``influences``) ``node#COLUMN`` biçiminde
uç noktalar taşır; bunlar ayrı bir grafta tutulur ki tablo seviyesi gezinme
kolon detayıyla karışmasın.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Sequence

import networkx as nx

from ..model import (
    CHECK_EDGES,
    COLUMN_EDGES,
    CONTROL_EDGES,
    DATA_EDGES,
    Edge,
    EdgeKind,
    Layer,
    Model,
    Node,
    parse_ref,
)


class ModelGraph:
    """``Model`` üzerine kurulu yönlü graf sarmalayıcısı."""

    def __init__(self, model: Model) -> None:
        self.model = model
        self.flow: nx.MultiDiGraph = nx.MultiDiGraph()
        self.columns: nx.MultiDiGraph = nx.MultiDiGraph()
        self._build()

    # -- kurulum ------------------------------------------------------------ #

    def _build(self) -> None:
        for node_id, node in self.model.nodes.items():
            self.flow.add_node(
                node_id,
                name=node.name,
                layer=node.layer.value,
                kind=node.kind.value,
                node_type=node.node_type.value,
                system=node.system,
                criticality=node.criticality,
                status=node.status.value,
            )

        for edge in self.model.edges:
            if edge.kind in COLUMN_EDGES:
                target_graph = self.columns
                source, target = edge.source, edge.target
                for ref in (source, target):
                    node_id, column = parse_ref(ref)
                    target_graph.add_node(ref, node_id=node_id, column=column)
            else:
                target_graph = self.flow
                source, target = edge.source_node, edge.target_node
                if source not in self.flow or target not in self.flow:
                    # Çözülemeyen referanslar doğrulama katmanının işi; graf onları
                    # sessizce atlar ki tek bozuk kenar tüm gezinmeyi düşürmesin.
                    continue
            target_graph.add_edge(source, target, key=edge.id, kind=edge.kind.value, edge=edge)

    # -- yardımcılar -------------------------------------------------------- #

    @staticmethod
    def _kinds(kinds: Iterable[EdgeKind] | None, default: Iterable[EdgeKind]) -> set[str]:
        return {k.value for k in (kinds if kinds is not None else default)}

    def _filtered(self, kinds: Iterable[EdgeKind] | None = None) -> nx.MultiDiGraph:
        """Yalnızca istenen kenar tiplerini içeren alt graf."""
        allowed = self._kinds(kinds, DATA_EDGES | CONTROL_EDGES)
        view = nx.MultiDiGraph()
        view.add_nodes_from(self.flow.nodes(data=True))
        for u, v, key, data in self.flow.edges(keys=True, data=True):
            if data["kind"] in allowed:
                view.add_edge(u, v, key=key, **data)
        return view

    def node(self, node_id: str) -> Node | None:
        return self.model.node(node_id)

    # -- gezinme ------------------------------------------------------------ #

    def upstream(
        self,
        node_id: str,
        hops: int | None = None,
        kinds: Iterable[EdgeKind] | None = None,
    ) -> set[str]:
        """Yukarı akış komşuları (kendisi hariç)."""
        return self._reachable(node_id, hops, kinds, reverse=True)

    def downstream(
        self,
        node_id: str,
        hops: int | None = None,
        kinds: Iterable[EdgeKind] | None = None,
    ) -> set[str]:
        """Aşağı akış komşuları (kendisi hariç)."""
        return self._reachable(node_id, hops, kinds, reverse=False)

    def _reachable(
        self,
        node_id: str,
        hops: int | None,
        kinds: Iterable[EdgeKind] | None,
        *,
        reverse: bool,
    ) -> set[str]:
        graph = self._filtered(kinds)
        if node_id not in graph:
            raise KeyError(f"node bulunamadı: {node_id}")
        if reverse:
            graph = graph.reverse(copy=False)
        lengths = nx.single_source_shortest_path_length(graph, node_id, cutoff=hops)
        return set(lengths) - {node_id}

    def neighborhood(
        self,
        node_id: str,
        hops: int = 2,
        kinds: Iterable[EdgeKind] | None = None,
    ) -> nx.MultiDiGraph:
        """Odak + N-hop komşuluk alt grafı (UI'ın sunucu tarafı alt-graf çağrısı)."""
        members = (
            self.upstream(node_id, hops, kinds) | self.downstream(node_id, hops, kinds) | {node_id}
        )
        return self._filtered(kinds).subgraph(members).copy()

    def paths(
        self,
        source: str,
        target: str,
        limit: int = 10,
        kinds: Iterable[EdgeKind] | None = None,
    ) -> list[list[str]]:
        """A'dan B'ye en kısa ``limit`` yol, kısa olan önce.

        Önceki sürüm ``cutoff=20`` ile tüm yolları hesaplayıp sıralıyordu: 20 kenardan
        uzun zincirde yanlış "yol yok" diyordu (inceleme #4) ve JOIN yoğun ağlarda yol
        sayısı katman başına katlandığı için sunucuyu kilitleyebiliyordu (#9).
        ``shortest_simple_paths`` bir üreticidir; yalnız istenen kadarını üretir.
        """
        graph = self._filtered(kinds if kinds is not None else DATA_EDGES)
        for ref in (source, target):
            if ref not in graph:
                raise KeyError(f"node bulunamadı: {ref}")
        simple = nx.DiGraph(graph)
        try:
            return list(itertools.islice(nx.shortest_simple_paths(simple, source, target), limit))
        except nx.NetworkXNoPath:
            return []

    def path_edges(self, path: Sequence[str], kinds: Iterable[EdgeKind] | None = None) -> list[Edge]:
        """Bir yol boyunca geçilen kenar nesneleri (adım paneli için)."""
        allowed = self._kinds(kinds, DATA_EDGES | CONTROL_EDGES)
        edges: list[Edge] = []
        for u, v in zip(path, path[1:], strict=False):
            for _, target, data in self.flow.out_edges(u, data=True):
                if target == v and data["kind"] in allowed:
                    edges.append(data["edge"])
                    break
        return edges

    # -- etki analizi ------------------------------------------------------- #

    def blast_radius(self, node_id: str) -> dict[str, set[str]]:
        """Bir node fail ederse etkilenenler (PLAN.md 8.5).

        Veri akışı ve yürütme sırası birlikte değerlendirilir: bir job patlarsa
        hem yazdığı tablolar hem de onu bekleyen job'lar etkilenir.
        """
        affected = self.downstream(node_id, kinds=DATA_EDGES | CONTROL_EDGES)
        datasets = {n for n in affected if n in self.model.datasets}
        processes = {n for n in affected if n in self.model.processes}
        consumption = {n for n in datasets if self.model.datasets[n].layer is Layer.CONSUMPTION}
        return {
            "all": affected,
            "datasets": datasets,
            "processes": processes,
            "consumption": consumption,
        }

    def column_lineage(self, ref: str, direction: str = "upstream") -> set[str]:
        """Kolon seviyesi izleme. ``ref`` = ``node_id#COLUMN``."""
        if ref not in self.columns:
            raise KeyError(f"kolon referansı bulunamadı: {ref}")
        graph = self.columns.reverse(copy=False) if direction == "upstream" else self.columns
        return set(nx.single_source_shortest_path_length(graph, ref)) - {ref}

    # -- yapısal sorgular --------------------------------------------------- #

    def execution_cycles(self) -> list[list[str]]:
        """Yürütme sırasında döngü (batch zinciri kilitlenmesi) — hata sayılır."""
        return [list(c) for c in nx.simple_cycles(self._filtered(CONTROL_EDGES))]

    def dataflow_cycles(self) -> list[list[str]]:
        """Veri akışında döngü — bazen meşru (geri besleme), o yüzden uyarı."""
        return [list(c) for c in nx.simple_cycles(self._filtered(DATA_EDGES))]

    def execution_order(self) -> list[str]:
        """Job'ların topolojik sırası. Döngü varsa ``nx.NetworkXUnfeasible`` yükselir."""
        control = self._filtered(CONTROL_EDGES)
        process_only = control.subgraph(self.model.processes).copy()
        return list(nx.topological_sort(process_only))

    def writers(self, dataset_id: str) -> list[str]:
        """Bu veri nesnesini yazan süreçler."""
        return [
            u
            for u, _, data in self.flow.in_edges(dataset_id, data=True)
            if data["kind"] in {EdgeKind.WRITES.value, EdgeKind.PRODUCES_FILE.value}
        ]

    def readers(self, dataset_id: str) -> list[str]:
        """Bu veri nesnesini okuyan süreçler."""
        return [
            v
            for _, v, data in self.flow.out_edges(dataset_id, data=True)
            if data["kind"] in {EdgeKind.READS.value, EdgeKind.CONSUMES_FILE.value}
        ]

    def checks_on(self, dataset_id: str) -> list[str]:
        """Bu veri nesnesine bağlı DQ / mutabakat süreçleri."""
        return [
            u
            for u, _, data in self.flow.in_edges(dataset_id, data=True)
            if data["kind"] in {k.value for k in CHECK_EDGES}
        ]

    # -- dışa aktarım ------------------------------------------------------- #

    def to_dot(self, kinds: Iterable[EdgeKind] | None = None) -> str:
        """Graphviz DOT (PLAN.md 7.4 statik export yolu)."""
        graph = self._filtered(kinds)
        lines = ["digraph dwhflow {", "  rankdir=LR;", '  node [shape=box, fontname="Segoe UI"];']
        for node_id, data in sorted(graph.nodes(data=True)):
            shape = "box" if data.get("node_type") == "dataset" else "ellipse"
            label = f'{data.get("name", node_id)}\\n[{data.get("layer", "?")}]'
            lines.append(f'  "{node_id}" [label="{label}", shape={shape}];')
        for u, v, data in graph.edges(data=True):
            style = "dashed" if data["kind"] in {k.value for k in CONTROL_EDGES} else "solid"
            lines.append(f'  "{u}" -> "{v}" [label="{data["kind"]}", style={style}];')
        lines.append("}")
        return "\n".join(lines)

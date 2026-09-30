"""Doküman okuyucularının ortak temeli: model kurucu, ad çözümleme, katman tahmini.

Her okuyucu (Excel, Word, SQL, …) bulduğu olguları bir ``ModelBuilder``'a ekler.
Kurucu:

* aynı tabloyu farklı yazımlarla (``dbo.STG_Account``, ``[STG_ACCOUNT]``,
  ``stg_account``) tek nesnede birleştirir,
* adından katman ve tür tahmin eder (``STG_`` → staging, ``F_`` → olgu …);
  adından anlaşılamazsa akıştaki konumuna bakar,
* her olguya nereden geldiğini (dosya, konum, okuyucu) bağlar,
* serbest metinden (kural ya da LLM) gelen olguları **taslak** işaretler; aynı
  olgu bir tablo ya da SQL'den de gelirse onaylı olur.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..model import (
    Column,
    Dataset,
    DatasetKind,
    Edge,
    EdgeKind,
    Layer,
    LoadStrategy,
    Model,
    Process,
    ProcessKind,
    Schedule,
    SourceRef,
    Status,
)
from .config import IngestConfig, active

# --------------------------------------------------------------------------- #
# Metin normalizasyonu
# --------------------------------------------------------------------------- #

_TR = str.maketrans("çğıöşüÇĞİÖŞÜâîû", "cgiosuCGIOSUaiu")


def fold(text: str) -> str:
    """Karşılaştırma anahtarı: küçük harf, Türkçe karakter ve aksan yok, tek boşluk."""
    value = str(text).translate(_TR)
    value = unicodedata.normalize("NFKD", value)
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def clean_name(raw: str) -> str:
    """Tablo/dosya adını görüntü biçimine getirir: tırnak ve köşeli parantez yok."""
    value = str(raw).strip().strip(";,")
    value = re.sub(r'[\[\]"`]', "", value)
    return re.sub(r"\s+", " ", value).strip()


def name_key(raw: str) -> str:
    """Birleştirme anahtarı: büyük harf, şema dahil."""
    return clean_name(raw).upper()


_FILE_NAME = re.compile(r"\.(dat|csv|txt|asc|ebc|bin|xml|json|gz|zip|out|fix)$", re.IGNORECASE)


def bare_key(key: str) -> str:
    """Şema önekini atar: ``DWH.STG_ACCOUNT`` → ``STG_ACCOUNT``.

    Dosya adındaki uzantı şema ayırıcısı değildir: ``KART_ISLEM.dat`` olduğu gibi kalır.
    """
    if _FILE_NAME.search(key):
        return key
    return key.rsplit(".", 1)[-1]


def is_file_name(key: str) -> bool:
    return bool(_FILE_NAME.search(key))


def make_id(raw: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", fold(raw).replace(" ", "_")).strip("_") or "nesne"
    candidate, index = base[:60], 2
    while candidate in taken:
        candidate = f"{base[:56]}_{index}"
        index += 1
    taken.add(candidate)
    return candidate


# --------------------------------------------------------------------------- #
# Katman ve tür tahmini (DWH adlandırma alışkanlıkları)
# --------------------------------------------------------------------------- #

_FILE_SUFFIX = re.compile(r"\.(dat|csv|txt|asc|ebc|bin|xml|json|gz|zip|out|fix)$", re.IGNORECASE)
_FILE_WORDS = ("_FILE", "DOSYA")


def explain_name(name: str, config: IngestConfig | None = None) -> dict[str, Any]:
    """Addan katman/tür tahmini ve **neden** o sonuca varıldığı (ayarlar ekranındaki test kutusu).

    Sıra: dosya uzantısı → kurallar (ayardakiler önce, sonra varsayılanlar) → dosya sözcükleri.
    Hiçbiri tutmazsa katman akıştaki konumdan belirlenir.
    """
    config = config or active()
    key = name_key(name)
    if is_file_name(key):
        return {"layer": Layer.INTERFACE, "kind": DatasetKind.INTERFACE_FILE,
                "reason": "dosya uzantısı", "rule": None}
    bare = bare_key(key)
    rule = config.match_rule(bare)
    if rule is not None:
        kind = rule.kind or _DEFAULT_KIND[rule.layer]
        return {"layer": rule.layer, "kind": kind, "reason": rule.describe(),
                "rule": {"type": rule.type, "value": rule.value, "origin": rule.origin}}
    if _FILE_SUFFIX.search(bare) or any(word in bare for word in _FILE_WORDS):
        return {"layer": Layer.INTERFACE, "kind": DatasetKind.INTERFACE_FILE,
                "reason": "addaki dosya sözcüğü", "rule": None}
    return {"layer": None, "kind": None, "reason": "kural yok; katman akıştaki konumdan belirlenir",
            "rule": None}


def infer_layer_kind(name: str) -> tuple[Layer | None, DatasetKind | None]:
    """Addan katman/tür tahmini. Emin değilse (None, None) — konum sonra karar verir."""
    result = explain_name(name)
    return result["layer"], result["kind"]


_DEFAULT_KIND = {
    Layer.SOURCE: DatasetKind.SOURCE_OBJECT, Layer.INTERFACE: DatasetKind.INTERFACE_FILE,
    Layer.STAGING: DatasetKind.STAGING_TABLE, Layer.ODS: DatasetKind.ODS_TABLE,
    Layer.CORE: DatasetKind.CORE_TABLE, Layer.MART: DatasetKind.MART_TABLE,
    Layer.CONSUMPTION: DatasetKind.REPORT,
}

_LOAD_WORDS: list[tuple[tuple[str, ...], LoadStrategy]] = [
    (("scd2", "scd 2", "scd type 2", "tarihsel"), LoadStrategy.SCD2),
    (("scd1", "scd 1", "scd type 1"), LoadStrategy.SCD1),
    (("cdc", "change data"), LoadStrategy.CDC),
    (("merge", "upsert"), LoadStrategy.MERGE),
    (("truncate",), LoadStrategy.TRUNCATE_LOAD),
    (("delta", "incremental", "artimli", "artirimli"), LoadStrategy.DELTA),
    (("append", "ekleme"), LoadStrategy.APPEND),
    (("full", "tam", "komple"), LoadStrategy.FULL),
]


def parse_load_strategy(text: str | None) -> LoadStrategy | None:
    if not text:
        return None
    folded = f" {fold(text)} "
    for words, strategy in _LOAD_WORDS:
        if any(f" {word} " in folded or folded.strip().startswith(word) for word in words):
            return strategy
    return None


# --------------------------------------------------------------------------- #
# Okuma raporu
# --------------------------------------------------------------------------- #


@dataclass
class DocReport:
    """Bir dokümandan ne çıkarıldığının dürüst özeti (galeri sayfasında gösterilir)."""

    file: str
    reader: str
    status: str = "ok"  # ok | empty | unsupported | error
    datasets: int = 0
    processes: int = 0
    flows: int = 0
    notes: list[str] = field(default_factory=list)
    llm_used: bool = False
    #: Kayıtlı biçim anahtarı (``registry.FormatSpec.key``); desteklenmiyorsa None.
    format: str | None = None
    #: Okuyucu nasıl seçildi: uzanti | icerik | zorla | yedek | yok
    detected: str = "uzanti"
    #: Arşivden çıkarıldıysa arşivin adı.
    container: str | None = None
    #: Tablo adı geçen ama kuralla çözülemeyen paragraf sayısı.
    leftovers: int = 0

    def note(self, message: str) -> None:
        if message not in self.notes:
            self.notes.append(message)

    def as_dict(self) -> dict[str, Any]:
        return {
            "file": self.file, "reader": self.reader, "status": self.status,
            "datasets": self.datasets, "processes": self.processes, "flows": self.flows,
            "notes": self.notes[:20], "llm_used": self.llm_used, "format": self.format,
            "detected": self.detected, "container": self.container, "leftovers": self.leftovers,
        }


def ref(file: Path | str, locator: str | None, extractor: str, *,
        confidence: float = 1.0, evidence: str | None = None) -> SourceRef:
    return SourceRef(
        file=Path(str(file)).name, locator=locator, extractor=extractor,
        confidence=confidence, evidence=(evidence[:300] if evidence else None),
    )


# --------------------------------------------------------------------------- #
# Model kurucu
# --------------------------------------------------------------------------- #


@dataclass
class _Node:
    display: str
    layer: Layer | None = None
    kind: Any = None
    system: str | None = None
    description: str | None = None
    tool: str | None = None
    load_strategy: LoadStrategy | None = None
    schedule: Schedule | None = None
    columns: dict[str, Column] = field(default_factory=dict)
    sources: list[SourceRef] = field(default_factory=list)
    confirmed: bool = False


class ModelBuilder:
    """Okuyuculardan gelen olguları biriktirip tek bir model üretir."""

    def __init__(self, name: str = "dokuman-modeli") -> None:
        self.name = name
        self.datasets: dict[str, _Node] = {}
        self.processes: dict[str, _Node] = {}
        self.edges: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.column_edges: dict[tuple[str, str], dict[str, Any]] = {}
        self.base: Model | None = None
        #: Tanınmayan tablolar: (dosya, konum, başlıklar, ilk 3 satır) — LLM başlık eşlemesi için.
        self.unrecognized_tables: list[tuple[str, str, list[str], list[list[str]]]] = []

    # -- ekleme ------------------------------------------------------------- #

    def dataset(self, raw_name: str, source: SourceRef | None = None, *, draft: bool = False,
                layer: Layer | None = None, kind: DatasetKind | None = None,
                system: str | None = None, description: str | None = None) -> str:
        key = name_key(raw_name)
        if not key:
            raise ValueError("boş tablo adı")
        node = self.datasets.get(key)
        if node is None:
            guessed_layer, guessed_kind = infer_layer_kind(key)
            node = _Node(display=clean_name(raw_name), layer=guessed_layer, kind=guessed_kind)
            self.datasets[key] = node
        if layer and not node.layer:
            node.layer = layer
        if kind and not node.kind:
            node.kind = kind
        node.system = node.system or active().system_name(system)
        node.description = node.description or description
        node.confirmed = node.confirmed or not draft
        if source:
            node.sources.append(source)
        return key

    def column(self, dataset_key: str, column_name: str, data_type: str | None = None, *,
               pk: bool = False) -> str:
        name = clean_name(column_name).upper()
        columns = self.datasets[dataset_key].columns
        if name and name not in columns:
            columns[name] = Column(name=name, data_type=data_type or None, pk=pk)
        elif name and pk:
            columns[name].pk = True
        return name

    def process(self, raw_name: str, source: SourceRef | None = None, *, draft: bool = False,
                kind: ProcessKind | None = None, tool: str | None = None,
                load_strategy: LoadStrategy | None = None, description: str | None = None,
                schedule: Schedule | None = None, system: str | None = None) -> str:
        key = name_key(raw_name)
        if not key:
            raise ValueError("boş süreç adı")
        node = self.processes.get(key)
        if node is None:
            node = _Node(display=clean_name(raw_name))
            self.processes[key] = node
        node.kind = node.kind or kind
        node.tool = node.tool or tool
        node.load_strategy = node.load_strategy or load_strategy
        node.description = node.description or description
        node.schedule = node.schedule or schedule
        node.system = node.system or active().system_name(system)
        node.confirmed = node.confirmed or not draft
        if source:
            node.sources.append(source)
        return key

    def flow(self, source_key: str, target_key: str, *, via: str | None = None,
             source: SourceRef | None = None, draft: bool = False,
             expression: str | None = None) -> bool:
        """``source`` tablosundan ``target`` tablosuna akış; ``via`` süreç anahtarı."""
        if source_key == target_key:
            return False
        if via:
            self._edge(EdgeKind.READS, source_key, via, source, draft)
            self._edge(EdgeKind.WRITES, via, target_key, source, draft)
        else:
            self._edge(EdgeKind.DERIVES_FROM, source_key, target_key, source, draft, expression)
        return True

    def reads(self, dataset_key: str, process_key: str, source: SourceRef | None = None,
              draft: bool = False) -> None:
        self._edge(EdgeKind.READS, dataset_key, process_key, source, draft)

    def writes(self, process_key: str, dataset_key: str, source: SourceRef | None = None,
               draft: bool = False) -> None:
        self._edge(EdgeKind.WRITES, process_key, dataset_key, source, draft)

    def precedes(self, first: str, second: str, source: SourceRef | None = None, *,
                 draft: bool = False) -> None:
        if first != second:
            self._edge(EdgeKind.PRECEDES, first, second, source, draft)

    def column_flow(self, src_key: str, src_col: str, tgt_key: str, tgt_col: str, *,
                    expression: str | None = None, source: SourceRef | None = None,
                    draft: bool = False) -> None:
        """Kolon eşlemesi. Serbest metin/LLM kaynaklıysa ``draft=True``: yapısal bir kaynak
        (STTM, SQL) aynı eşlemeyi de verirse onaylı olur."""
        s_col, t_col = self.column(src_key, src_col), self.column(tgt_key, tgt_col)
        if not (s_col and t_col):
            return
        entry = self.column_edges.setdefault(
            (f"{src_key}#{s_col}", f"{tgt_key}#{t_col}"),
            {"expression": expression, "sources": [], "confirmed": False},
        )
        entry["confirmed"] = entry["confirmed"] or not draft
        if source:
            entry["sources"].append(source)

    def _edge(self, kind: EdgeKind, source_key: str, target_key: str,
              source: SourceRef | None, draft: bool, expression: str | None = None) -> None:
        entry = self.edges.setdefault(
            (kind.value, source_key, target_key),
            {"sources": [], "confirmed": False, "expression": expression},
        )
        entry["confirmed"] = entry["confirmed"] or not draft
        if source:
            entry["sources"].append(source)

    def merge_model(self, model: Model) -> None:
        """Doğrudan yüklenen YAML modelini temel alır; dokümanlar onun üstüne eklenir."""
        self.base = model if self.base is None else _merge_models(self.base, model)

    @property
    def empty(self) -> bool:
        return not (self.datasets or self.processes or self.base)

    # -- çözümleme ---------------------------------------------------------- #

    def _merge_aliases(self) -> dict[str, str]:
        """Şemasız adı, tek bir şemalı karşılığı varsa ona bağlar (STG_X → DWH.STG_X)."""
        qualified: dict[str, list[str]] = {}
        for key in self.datasets:
            if "." in key and not is_file_name(key):
                qualified.setdefault(bare_key(key), []).append(key)
        alias = {key: qualified[key][0] for key in self.datasets
                 if ("." not in key or is_file_name(key)) and len(qualified.get(key, [])) == 1}
        for old, new in alias.items():
            source, target = self.datasets.pop(old), self.datasets[new]
            target.sources.extend(source.sources)
            for name, column in source.columns.items():
                target.columns.setdefault(name, column)
            target.layer, target.kind = target.layer or source.layer, target.kind or source.kind
            target.confirmed = target.confirmed or source.confirmed
        return alias

    def _infer_layers(self, alias: dict[str, str]) -> None:
        """Adından katmanı anlaşılamayan nesneleri akıştaki konumuna göre yerleştirir.

        Hiç beslenmeyen nesne kaynaktır; diğerleri, kendisini besleyen en üst
        katmanın bir sonrasına konur (arayüz katmanı dosyalara ayrıldığı için
        tablolar için atlanır, tüketim katmanına ise yalnız rapor/çıkış türleri gider).
        """
        def canon(key: str) -> str:
            return alias.get(key, key)

        process_inputs: dict[str, set[str]] = {}
        for (kind, source, target) in self.edges:
            if kind == EdgeKind.READS.value:
                process_inputs.setdefault(target, set()).add(canon(source))
        upstream: dict[str, set[str]] = {}
        for (kind, source, target) in self.edges:
            if kind == EdgeKind.WRITES.value:
                upstream.setdefault(canon(target), set()).update(process_inputs.get(source, ()))
            elif kind == EdgeKind.DERIVES_FROM.value:
                upstream.setdefault(canon(target), set()).add(canon(source))

        order = list(Layer)
        ceiling = order.index(Layer.MART)
        for _ in range(len(self.datasets) + 1):
            changed = False
            for key, node in self.datasets.items():
                if node.layer is not None:
                    continue
                parents = [p for p in upstream.get(key, ()) if p in self.datasets and p != key]
                if not parents:
                    node.layer, changed = Layer.SOURCE, True
                    continue
                known = [self.datasets[p].layer for p in parents if self.datasets[p].layer]
                if len(known) == len(parents):
                    step = max(order.index(layer) for layer in known) + 1
                    if order[min(step, len(order) - 1)] is Layer.INTERFACE:
                        step += 1
                    node.layer, changed = order[min(step, ceiling)], True
            if not changed:
                break
        for node in self.datasets.values():
            node.layer = node.layer or Layer.CORE
            node.kind = node.kind or _DEFAULT_KIND[node.layer]

    def build(self) -> Model:
        alias = self._merge_aliases()
        self._infer_layers(alias)

        def canon(key: str) -> str:
            return alias.get(key, key)

        model = self.base.model_copy(deep=True) if self.base else Model(name=self.name)
        taken = set(model.nodes)
        existing = {name_key(node.name): node.id for node in model.nodes.values()}
        dataset_ids = {key: existing.get(key) or make_id(node.display, taken)
                       for key, node in self.datasets.items()}
        process_ids = {key: existing.get(key) or make_id(node.display, taken)
                       for key, node in self.processes.items()}

        for key, node in self.datasets.items():
            node_id = dataset_ids[key]
            if node_id in model.datasets:
                model.datasets[node_id].sources.extend(node.sources[:8])
                continue
            model.add(Dataset(
                id=node_id, name=node.display, kind=node.kind, layer=node.layer,
                system=node.system, description=node.description,
                columns=list(node.columns.values()), sources=node.sources[:12],
                status=Status.CONFIRMED if node.confirmed else Status.DRAFT,
            ))
        for key, node in self.processes.items():
            node_id = process_ids[key]
            if node_id in model.processes or node_id in model.datasets:
                continue
            model.add(Process(
                id=node_id, name=node.display, kind=node.kind or ProcessKind.ETL_MAPPING,
                layer=self._process_layer(key, alias), tool=node.tool,
                load_strategy=node.load_strategy, description=node.description,
                schedule=node.schedule, sources=node.sources[:12], system=node.system,
                status=Status.CONFIRMED if node.confirmed else Status.DRAFT,
            ))

        def node_id(key: str) -> str | None:
            return dataset_ids.get(canon(key)) or process_ids.get(key)

        known = {edge.id for edge in model.edges}
        for (kind, source, target), entry in self.edges.items():
            source_id, target_id = node_id(source), node_id(target)
            if not (source_id and target_id):
                continue
            edge = Edge(
                kind=EdgeKind(kind), source=source_id, target=target_id,
                status=Status.CONFIRMED if entry["confirmed"] else Status.DRAFT,
                expression=entry.get("expression"), sources=entry["sources"][:8],
            )
            if edge.id not in known:
                model.edges.append(edge)
                known.add(edge.id)

        for (source_ref, target_ref), entry in self.column_edges.items():
            s_key, s_col = source_ref.split("#", 1)
            t_key, t_col = target_ref.split("#", 1)
            source_id, target_id = dataset_ids.get(canon(s_key)), dataset_ids.get(canon(t_key))
            if not (source_id and target_id):
                continue
            edge = Edge(kind=EdgeKind.COLUMN_MAPS_TO, source=f"{source_id}#{s_col}",
                        target=f"{target_id}#{t_col}", expression=entry.get("expression"),
                        sources=entry["sources"][:4],
                        status=Status.CONFIRMED if entry.get("confirmed", True) else Status.DRAFT)
            if edge.id not in known:
                model.edges.append(edge)
                known.add(edge.id)
        return model

    def _process_layer(self, process_key: str, alias: dict[str, str]) -> Layer:
        """Süreç, yazdığı tablonun katmanında koşar sayılır."""
        for (kind, source, target) in self.edges:
            if kind == EdgeKind.WRITES.value and source == process_key:
                node = self.datasets.get(alias.get(target, target))
                if node and node.layer:
                    return node.layer
        return Layer.STAGING


def _merge_models(first: Model, second: Model) -> Model:
    merged = first.model_copy(deep=True)
    for system in second.systems.values():
        merged.systems.setdefault(system.id, system)
    for dataset in second.datasets.values():
        if dataset.id not in merged.processes:
            merged.datasets.setdefault(dataset.id, dataset)
    for process in second.processes.values():
        if process.id not in merged.datasets:
            merged.processes.setdefault(process.id, process)
    known = {edge.id for edge in merged.edges}
    merged.edges.extend(edge for edge in second.edges if edge.id not in known)
    return merged

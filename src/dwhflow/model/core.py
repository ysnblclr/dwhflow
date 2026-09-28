"""Kanonik veri modeli (PLAN.md Bölüm 3).

Tasarım kararları:

* ``extra="forbid"``: YAML'daki yazım hataları sessizce yutulmaz, hemen hata verir.
  Bu araç spesifikasyon güdümlü olduğu için erken hata tercih edilir.
* Node id'leri ``[a-z0-9_]`` ile sınırlı — nokta ve ``#`` yasak. Sebep: kolon
  referansları ``node_id#COLUMN`` biçiminde yazılıyor, ayrıştırma belirsiz olmasın.
* Her node ve edge ``sources`` (provenance) ve ``status`` taşır. Bir kenarın nereden
  geldiği (STTM mi, SQL mi, LLM mi) modelin kalıcı parçasıdır; denetimde gerekir.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import (
    DatasetKind,
    DeltaMethod,
    EdgeKind,
    Layer,
    LoadStrategy,
    NodeType,
    ProcessKind,
    Sensitivity,
    Status,
    SystemKind,
)

ID_PATTERN = r"^[a-z0-9][a-z0-9_]*$"
COLUMN_SEP = "#"

#: Her kenar tipinin uç noktalarında ne tür bir referans beklendiği.
#: "column" = ``node_id#COLUMN`` biçimi, "any" = serbest.
EndpointType = Literal["dataset", "process", "column", "any"]

ENDPOINT_SPEC: dict[EdgeKind, tuple[EndpointType, EndpointType]] = {
    EdgeKind.READS: ("dataset", "process"),
    EdgeKind.WRITES: ("process", "dataset"),
    EdgeKind.PRODUCES_FILE: ("process", "dataset"),
    EdgeKind.CONSUMES_FILE: ("dataset", "process"),
    EdgeKind.TRANSFERS: ("dataset", "dataset"),
    EdgeKind.PRECEDES: ("process", "process"),
    EdgeKind.TRIGGERS: ("any", "process"),
    EdgeKind.COLUMN_MAPS_TO: ("column", "column"),
    EdgeKind.INFLUENCES: ("column", "column"),
    EdgeKind.DERIVES_FROM: ("dataset", "dataset"),
    EdgeKind.VALIDATES: ("process", "dataset"),
    EdgeKind.RECONCILES: ("process", "dataset"),
    EdgeKind.IMPLEMENTS: ("any", "any"),
}


def parse_ref(ref: str) -> tuple[str, str | None]:
    """``"stg_account#ACCOUNT_ID"`` -> ``("stg_account", "ACCOUNT_ID")``."""
    node_id, sep, column = ref.partition(COLUMN_SEP)
    return node_id, (column if sep else None)


def make_ref(node_id: str, column: str | None = None) -> str:
    return f"{node_id}{COLUMN_SEP}{column}" if column else node_id


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False, validate_assignment=True)


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #


class SourceRef(Base):
    """Bir bilginin nereden geldiği. PLAN.md 5.10.

    ``extractor`` örnekleri: ``manual``, ``excel_sttm``, ``sql:sqlglot``,
    ``autosys_jil``, ``odbc:information_schema``, ``llm:qwen3-8b@extract_edges/v3``.
    """

    file: str | None = None
    locator: str | None = Field(
        default=None, description="Sayfa/satır/hücre — ör. 'Sheet1!B12' veya 'p.4 §2.1'"
    )
    extractor: str = "manual"
    evidence: str | None = Field(default=None, description="Kanıt cümlesi (LLM için zorunlu)")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    extracted_at: datetime | None = None

    @property
    def is_llm(self) -> bool:
        return self.extractor.startswith("llm:")


#: Güven hiyerarşisi (PLAN.md 5.10). Küçük sayı = daha güvenilir.
EXTRACTOR_RANK: dict[str, int] = {
    "odbc": 0,
    "sql": 1,
    "loader": 1,
    "etl": 2,
    "scheduler": 2,
    "excel_sttm": 3,
    "manual": 3,
    "doc": 4,
    "llm": 5,
}


def extractor_rank(extractor: str) -> int:
    prefix = extractor.split(":", 1)[0]
    return EXTRACTOR_RANK.get(prefix, 4)


# --------------------------------------------------------------------------- #
# Yardımcı yapılar
# --------------------------------------------------------------------------- #


class Column(Base):
    name: str
    data_type: str | None = None
    nullable: bool | None = None
    pk: bool = False
    description: str | None = None
    sensitivity: Sensitivity = Sensitivity.INTERNAL


class FileSpec(Base):
    """Interface/export dosyası yerleşimi (PLAN.md 3.4, 5.6)."""

    format: str | None = Field(default=None, description="fixed_width | csv | binary | xml | json")
    encoding: str | None = Field(default=None, description="utf-8 | cp037 (EBCDIC) | ascii")
    record_length: int | None = None
    variable_length: bool | None = None
    copybook: str | None = Field(default=None, description="COBOL copybook dosya yolu")
    name_pattern: str | None = Field(default=None, description="ör. ACCT_YYYYMMDD_01.dat")
    expected_arrival: str | None = Field(default=None, description="HH:MM")
    has_trailer: bool | None = Field(default=None, description="Satır sayısı/checksum kaydı var mı")
    compression: str | None = None
    encryption: str | None = None


class Schedule(Base):
    cron: str | None = None
    start_times: list[str] = Field(default_factory=list, description="HH:MM listesi")
    days_of_week: list[str] = Field(default_factory=list)
    calendar: str | None = None
    event: str | None = Field(default=None, description="Olay tetiklemesi açıklaması")


class Runtime(Base):
    """Çalışma süresi istatistikleri — simülasyonun (M4) girdisi."""

    avg_min: float | None = None
    p50_min: float | None = None
    p95_min: float | None = None
    min_min: float | None = None
    max_min: float | None = None
    typical_rows: int | None = None

    @property
    def best_estimate_min(self) -> float | None:
        """Simülasyon için en iyi tek nokta tahmini."""
        return self.p50_min if self.p50_min is not None else self.avg_min


class Sla(Base):
    target_time: str | None = Field(default=None, description="HH:MM — bu saate kadar bitmeli")
    description: str | None = None


class Retry(Base):
    max_attempts: int = 1
    delay_min: float = 0.0


# --------------------------------------------------------------------------- #
# Node'lar
# --------------------------------------------------------------------------- #


class NodeBase(Base):
    id: str = Field(pattern=ID_PATTERN)
    name: str
    layer: Layer
    system: str | None = Field(default=None, description="System.id referansı")
    description: str | None = None
    owner: str | None = None
    criticality: int = Field(default=3, ge=1, le=5)
    environment: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: Status = Status.CONFIRMED
    sources: list[SourceRef] = Field(default_factory=list)
    attrs: dict[str, Any] = Field(default_factory=dict)

    @property
    def best_source_rank(self) -> int:
        """En güvenilir kaynağın rütbesi; kaynak yoksa 'manual' sayılır."""
        if not self.sources:
            return EXTRACTOR_RANK["manual"]
        return min(extractor_rank(s.extractor) for s in self.sources)


class Dataset(NodeBase):
    node_type: Literal[NodeType.DATASET] = NodeType.DATASET
    kind: DatasetKind
    columns: list[Column] = Field(default_factory=list)
    grain: str | None = None
    row_estimate: int | None = None
    daily_growth_rows: int | None = None
    size_mb: float | None = None
    retention: str | None = None
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    partitioning: str | None = None
    file_spec: FileSpec | None = None
    ddl_ref: str | None = None

    @field_validator("columns")
    @classmethod
    def _unique_column_names(cls, columns: list[Column]) -> list[Column]:
        seen: set[str] = set()
        for column in columns:
            if column.name in seen:
                raise ValueError(f"tekrarlanan kolon adı: {column.name}")
            seen.add(column.name)
        return columns

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)


class Process(NodeBase):
    node_type: Literal[NodeType.PROCESS] = NodeType.PROCESS
    kind: ProcessKind
    tool: str | None = Field(default=None, description="ör. Informatica, TPT, AutoSys")
    load_strategy: LoadStrategy | None = None
    delta_method: DeltaMethod | None = None
    watermark_column: str | None = None
    schedule: Schedule | None = None
    runtime: Runtime | None = None
    sla: Sla | None = None
    restartable: bool | None = None
    idempotent: bool | None = None
    parallelism: int | None = Field(default=None, ge=1)
    resources: dict[str, int] = Field(default_factory=dict)
    retry: Retry | None = None
    parent_job: str | None = Field(default=None, description="box job hiyerarşisi")
    code_ref: str | None = None
    expression: str | None = None


Node = Dataset | Process


class System(Base):
    id: str = Field(pattern=ID_PATTERN)
    name: str
    kind: SystemKind
    description: str | None = None
    owner: str | None = None
    host: str | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Edge
# --------------------------------------------------------------------------- #


class Edge(Base):
    """Akış yönünde yazılmış kenar. Bkz. EdgeKind docstring."""

    kind: EdgeKind
    source: str
    target: str
    id: str | None = None
    expression: str | None = Field(default=None, description="Dönüşüm ifadesi")
    transform_type: str | None = Field(
        default=None, description="direct | cast | lookup | aggregate | case | formula"
    )
    status: Status = Status.CONFIRMED
    sources: list[SourceRef] = Field(default_factory=list)
    attrs: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _default_id(self) -> Edge:
        if self.id is None:
            # Deterministik id: aynı kenar tekrar çıkarıldığında aynı id'yi alır.
            # Karar önbelleği (M2d) ve diff (M5) buna dayanır.
            object.__setattr__(self, "id", f"{self.kind.value}:{self.source}>{self.target}")
        return self

    @property
    def source_node(self) -> str:
        return parse_ref(self.source)[0]

    @property
    def target_node(self) -> str:
        return parse_ref(self.target)[0]

    @property
    def best_source_rank(self) -> int:
        if not self.sources:
            return EXTRACTOR_RANK["manual"]
        return min(extractor_rank(s.extractor) for s in self.sources)

    @property
    def is_llm_only(self) -> bool:
        return bool(self.sources) and all(s.is_llm for s in self.sources)


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #


class Model(Base):
    """Tüm modelin bellek içi hâli."""

    name: str = "dwhflow-model"
    systems: dict[str, System] = Field(default_factory=dict)
    datasets: dict[str, Dataset] = Field(default_factory=dict)
    processes: dict[str, Process] = Field(default_factory=dict)
    edges: list[Edge] = Field(default_factory=list)

    @model_validator(mode="after")
    def _keys_match_ids(self) -> Model:
        for collection_name in ("systems", "datasets", "processes"):
            for key, item in getattr(self, collection_name).items():
                if key != item.id:
                    raise ValueError(
                        f"{collection_name}: sözlük anahtarı '{key}' ile id '{item.id}' uyuşmuyor"
                    )
        overlap = set(self.datasets) & set(self.processes)
        if overlap:
            raise ValueError(f"aynı id hem dataset hem process: {sorted(overlap)}")
        return self

    # -- erişim ------------------------------------------------------------- #

    @property
    def nodes(self) -> dict[str, Node]:
        return {**self.datasets, **self.processes}

    def node(self, node_id: str) -> Node | None:
        return self.datasets.get(node_id) or self.processes.get(node_id)

    def node_type_of(self, node_id: str) -> EndpointType | None:
        if node_id in self.datasets:
            return "dataset"
        if node_id in self.processes:
            return "process"
        return None

    def edges_of(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if node_id in (e.source_node, e.target_node)]

    # -- değiştirme --------------------------------------------------------- #

    def add(self, item: Dataset | Process | System | Edge) -> None:
        if isinstance(item, Dataset):
            self.datasets[item.id] = item
        elif isinstance(item, Process):
            self.processes[item.id] = item
        elif isinstance(item, System):
            self.systems[item.id] = item
        else:
            self.edges.append(item)

    def stats(self) -> dict[str, int]:
        return {
            "systems": len(self.systems),
            "datasets": len(self.datasets),
            "processes": len(self.processes),
            "edges": len(self.edges),
            "columns": sum(len(d.columns) for d in self.datasets.values()),
        }

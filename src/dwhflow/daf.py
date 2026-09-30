"""DAF — DWHFlow Ara Formatı (PLAN_V2 §3.6).

LLM'in (ve istenirse her okuyucunun) ürettiği ortak çıkarım biçimi. İki şema:

* ``dwhflow.extract/2`` — tablolar, süreçler, kolon eşlemeli akışlar, job
  bağımlılıkları ve "insan bakmalı" belirsizlikler. Her öğe belgeden birebir kanıt taşır.
* ``dwhflow.table-roles/1`` — tanınmayan bir tablonun sütun başlıklarının rolleri.

Buradaki modeller yalnız **biçimi** doğrular (şema, enum). Kanıtın belgede birebir
geçip geçmediği gibi içerik denetimleri LLM katmanındadır (``llm``). Geçersiz enum
değerleri reddedilmez, ``None``'a çekilir: tür/katman boş kalırsa kurallar doldurur.
"""

from __future__ import annotations

import inspect
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .ingest.base import ModelBuilder, ref
from .model import DatasetKind, Layer, LoadStrategy, ProcessKind, Schedule

EXTRACT_SCHEMA = "dwhflow.extract/2"
TABLE_ROLES_SCHEMA = "dwhflow.table-roles/1"
TABLE_KINDS = ("mapping", "job_dependencies", "column_layout", "file_layout", "other")


class _Item(BaseModel):
    # LLM çıktısındaki fazladan alanlar yok sayılır; eksik zorunlu alan hatadır.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


def _enum_or_none(enum: type, value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return enum(str(value).strip().lower()).value
    except ValueError:
        return None


class DafColumn(_Item):
    name: str = Field(min_length=1)
    type: str | None = None
    pk: bool = False


class DafDataset(_Item):
    name: str = Field(min_length=1)
    kind: str | None = None
    layer: str | None = None
    system: str | None = None
    columns: list[DafColumn] = Field(default_factory=list)
    evidence: str = Field(min_length=1)

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, value: Any) -> str | None:
        return _enum_or_none(DatasetKind, value)

    @field_validator("layer", mode="before")
    @classmethod
    def _layer(cls, value: Any) -> str | None:
        return _enum_or_none(Layer, value)


class DafSchedule(_Item):
    times: list[str] = Field(default_factory=list)
    calendar: str | None = None


class DafProcess(_Item):
    name: str = Field(min_length=1)
    kind: str | None = None
    tool: str | None = None
    load_strategy: str | None = None
    schedule: DafSchedule | None = None
    evidence: str = Field(min_length=1)

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, value: Any) -> str | None:
        return _enum_or_none(ProcessKind, value)

    @field_validator("load_strategy", mode="before")
    @classmethod
    def _load(cls, value: Any) -> str | None:
        return _enum_or_none(LoadStrategy, value)


class DafColumnMap(_Item):
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    expression: str | None = None


class DafFlow(_Item):
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    process: str | None = None
    columns: list[DafColumnMap] = Field(default_factory=list)
    evidence: str = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value: Any) -> float:
        try:
            return min(1.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return 0.5


class DafDependency(_Item):
    before: str = Field(min_length=1)
    after: str = Field(min_length=1)
    condition: str | None = None
    evidence: str = Field(min_length=1)


class DafUnresolved(_Item):
    text: str = Field(min_length=1)
    reason: str = ""


class ExtractDoc(_Item):
    schema_: Literal["dwhflow.extract/2"] = Field(default=EXTRACT_SCHEMA, alias="schema")
    datasets: list[DafDataset] = Field(default_factory=list)
    processes: list[DafProcess] = Field(default_factory=list)
    flows: list[DafFlow] = Field(default_factory=list)
    dependencies: list[DafDependency] = Field(default_factory=list)
    unresolved: list[DafUnresolved] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    @property
    def empty(self) -> bool:
        return not (self.datasets or self.processes or self.flows or self.dependencies)


class TableRoles(_Item):
    schema_: Literal["dwhflow.table-roles/1"] = Field(default=TABLE_ROLES_SCHEMA, alias="schema")
    table_kind: Literal["mapping", "job_dependencies", "column_layout", "file_layout", "other"]
    header_row: int = Field(default=1, ge=1)
    #: başlık metni → rol (``ingest.config.DEFAULT_ROLES`` anahtarları)
    roles: dict[str, str] = Field(default_factory=dict)
    unmapped: list[str] = Field(default_factory=list)
    evidence: str = ""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


def supports_draft_columns(builder: Any) -> bool:
    """Model kurucunun ``column_flow``'u kolon kenarını taslak işaretleyebiliyor mu (``draft`` alır mı)?"""
    try:
        return "draft" in inspect.signature(builder.column_flow).parameters
    except (AttributeError, TypeError, ValueError):
        return False


def apply_extract(builder: ModelBuilder, doc: ExtractDoc, *, file: str, locator: str | None,
                  extractor: str, draft: bool = True, confidence_cap: float = 0.8) -> dict[str, int]:
    """Doğrulanmış bir ``extract/2`` belgesini model kurucuya ekler; eklenen öğe sayılarını döner.

    Serbest metin/LLM kaynaklı öğeler ``draft=True`` ile **taslak** girer; aynı olgu yapısal
    bir kaynaktan da gelirse kurucu onu onaylı yapar. Güven ``confidence_cap``'e kırpılır.

    Kolon eşlemeleri: kurucunun ``column_flow``'u ``draft`` almıyorsa taslak kolon eşlemeleri
    (onaylı görünmemeleri için) eklenmez ve ``columns_skipped``'te sayılır.
    """

    def source(evidence: str, confidence: float = 1.0):
        return ref(file, locator, extractor, confidence=min(confidence, confidence_cap),
                   evidence=evidence)

    counts = {"datasets": 0, "processes": 0, "flows": 0, "columns": 0, "dependencies": 0,
              "columns_skipped": 0}
    column_drafts = supports_draft_columns(builder)
    dataset_keys: dict[str, str] = {}

    def dataset(name: str, evidence: str, confidence: float = 1.0) -> str:
        key = builder.dataset(name, source(evidence, confidence), draft=draft)
        dataset_keys[name] = key
        return key

    for item in doc.datasets:
        key = builder.dataset(
            item.name, source(item.evidence), draft=draft,
            layer=Layer(item.layer) if item.layer else None,
            kind=DatasetKind(item.kind) if item.kind else None, system=item.system,
        )
        for column in item.columns:
            builder.column(key, column.name, column.type, pk=column.pk)
        dataset_keys[item.name] = key
        counts["datasets"] += 1

    process_keys: dict[str, str] = {}
    for item in doc.processes:
        schedule = None
        if item.schedule and (item.schedule.times or item.schedule.calendar):
            schedule = Schedule(start_times=item.schedule.times, calendar=item.schedule.calendar)
        process_keys[item.name] = builder.process(
            item.name, source(item.evidence), draft=draft,
            kind=ProcessKind(item.kind) if item.kind else None, tool=item.tool,
            load_strategy=LoadStrategy(item.load_strategy) if item.load_strategy else None,
            schedule=schedule,
        )
        counts["processes"] += 1

    for flow in doc.flows:
        src = dataset_keys.get(flow.source) or dataset(flow.source, flow.evidence, flow.confidence)
        tgt = dataset_keys.get(flow.target) or dataset(flow.target, flow.evidence, flow.confidence)
        via = None
        if flow.process:
            via = process_keys.get(flow.process) or builder.process(
                flow.process, source(flow.evidence, flow.confidence), draft=draft)
        if builder.flow(src, tgt, via=via, source=source(flow.evidence, flow.confidence), draft=draft):
            counts["flows"] += 1
        for column in flow.columns:
            if draft and not column_drafts:
                # Kurucu kolon kenarını taslak işaretleyemiyorsa onaylı olarak eklemektense eklenmez:
                # LLM/serbest metin çıkarımı hiçbir zaman onaylı girmemeli.
                counts["columns_skipped"] += 1
                continue
            extra = {"draft": draft} if column_drafts else {}
            builder.column_flow(src, column.source, tgt, column.target, expression=column.expression,
                                source=source(flow.evidence, flow.confidence), **extra)
            counts["columns"] += 1

    for dependency in doc.dependencies:
        first = process_keys.get(dependency.before) or builder.process(
            dependency.before, source(dependency.evidence), draft=draft)
        second = process_keys.get(dependency.after) or builder.process(
            dependency.after, source(dependency.evidence), draft=draft)
        builder.precedes(first, second, source(dependency.evidence), draft=draft)
        counts["dependencies"] += 1
    return counts

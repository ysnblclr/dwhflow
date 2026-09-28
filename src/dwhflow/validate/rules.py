"""Model tutarlılık kuralları (PLAN.md 8.7).

Her kural bir jeneratör: modeli alır, ``Finding`` üretir. Kural eklemek için
fonksiyonu yaz ve ``ALL_RULES``a ekle — başka yeri değiştirmek gerekmez.

Şiddet seçimi ilkesi:

* ``ERROR``   — model yapısal olarak bozuk, gezinme/simülasyon güvenilmez.
* ``WARNING`` — büyük olasılıkla hata ama meşru istisnası olabilir.
* ``INFO``    — eksik bilgi / dikkat çekilmesi gereken durum, yanlış değil.
"""

from __future__ import annotations

from collections.abc import Iterator

import networkx as nx
from pydantic import BaseModel, ConfigDict

from ..graph import ModelGraph
from ..model import (
    CHECK_EDGES,
    COLUMN_EDGES,
    CONTROL_EDGES,
    DATA_EDGES,
    ENDPOINT_SPEC,
    EXPECTED_LAYER,
    FILE_KINDS,
    LAYER_ORDER,
    DatasetKind,
    EdgeKind,
    Layer,
    Model,
    ProcessKind,
    Severity,
    Status,
    parse_ref,
)


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule: str
    severity: Severity
    message: str
    subject: str | None = None

    def __str__(self) -> str:
        where = f" [{self.subject}]" if self.subject else ""
        return f"{self.severity.value.upper():7} {self.rule}{where}: {self.message}"


# --------------------------------------------------------------------------- #
# Referans bütünlüğü
# --------------------------------------------------------------------------- #


def rule_unresolved_refs(model: Model, graph: ModelGraph) -> Iterator[Finding]:
    """Kenar uç noktası modelde yok."""
    for edge in model.edges:
        for role, ref in (("source", edge.source), ("target", edge.target)):
            node_id, column = parse_ref(ref)
            node = model.node(node_id)
            if node is None:
                yield Finding(
                    rule="unresolved_ref",
                    severity=Severity.ERROR,
                    subject=edge.id,
                    message=f"{role} '{node_id}' modelde tanımlı değil",
                )
                continue
            if column is not None:
                if node_id in model.processes:
                    yield Finding(
                        rule="unresolved_ref",
                        severity=Severity.ERROR,
                        subject=edge.id,
                        message=f"{role} '{ref}' bir sürece kolon referansı veriyor",
                    )
                elif node.columns and node.column(column) is None:
                    yield Finding(
                        rule="unknown_column",
                        severity=Severity.ERROR,
                        subject=edge.id,
                        message=f"{role} '{node_id}' tablosunda '{column}' kolonu yok",
                    )


def rule_endpoint_types(model: Model, graph: ModelGraph) -> Iterator[Finding]:
    """Kenar tipi ile uç nokta tipleri uyuşmuyor (ör. ``writes`` tablodan çıkıyor)."""
    for edge in model.edges:
        expected_source, expected_target = ENDPOINT_SPEC[edge.kind]
        for role, ref, expected in (
            ("source", edge.source, expected_source),
            ("target", edge.target, expected_target),
        ):
            node_id, column = parse_ref(ref)
            actual = model.node_type_of(node_id)
            if actual is None:
                continue  # unresolved_ref kuralı zaten bildirdi
            if expected == "column":
                if column is None:
                    yield Finding(
                        rule="endpoint_type",
                        severity=Severity.ERROR,
                        subject=edge.id,
                        message=(
                            f"'{edge.kind.value}' kenarı {role} tarafında kolon referansı "
                            f"bekler (node#KOLON), '{ref}' geldi"
                        ),
                    )
            elif expected != "any" and actual != expected:
                yield Finding(
                    rule="endpoint_type",
                    severity=Severity.ERROR,
                    subject=edge.id,
                    message=(
                        f"'{edge.kind.value}' kenarı {role} tarafında {expected} bekler, "
                        f"'{node_id}' bir {actual}"
                    ),
                )


def rule_system_refs(model: Model, graph: ModelGraph) -> Iterator[Finding]:
    """Node'un işaret ettiği sistem tanımlı değil."""
    for node_id, node in model.nodes.items():
        if node.system and node.system not in model.systems:
            yield Finding(
                rule="unknown_system",
                severity=Severity.WARNING,
                subject=node_id,
                message=f"'{node.system}' sistemi tanımlı değil",
            )


# --------------------------------------------------------------------------- #
# Akış bütünlüğü
# --------------------------------------------------------------------------- #

#: Kaynak/tüketim uçlarında yazar/okuyucu beklemeyiz.
_NO_WRITER_OK = frozenset({DatasetKind.SOURCE_OBJECT, DatasetKind.DOWNSTREAM_SYSTEM})
_NO_READER_OK = frozenset(
    {
        DatasetKind.REPORT,
        DatasetKind.DOWNSTREAM_SYSTEM,
        DatasetKind.ARCHIVE_TABLE,
        DatasetKind.REJECT_TABLE,
        DatasetKind.CONTROL_TABLE,
    }
)


def rule_orphan_datasets(model: Model, graph: ModelGraph) -> Iterator[Finding]:
    """Bu tabloyu kimse doldurmuyor — genelde dokümanda eksik bir job demektir."""
    for dataset_id, dataset in model.datasets.items():
        if dataset.kind in _NO_WRITER_OK or dataset.layer is Layer.SOURCE:
            continue
        if not graph.writers(dataset_id):
            yield Finding(
                rule="no_writer",
                severity=Severity.WARNING,
                subject=dataset_id,
                message="bu veri nesnesini yazan bir süreç yok",
            )


def rule_dead_datasets(model: Model, graph: ModelGraph) -> Iterator[Finding]:
    """Bu tabloyu kimse okumuyor — ölü tablo olabilir."""
    for dataset_id, dataset in model.datasets.items():
        if dataset.kind in _NO_READER_OK or dataset.layer is Layer.CONSUMPTION:
            continue
        if not graph.readers(dataset_id) and not _has_outgoing_transfer(graph, dataset_id):
            yield Finding(
                rule="no_reader",
                severity=Severity.INFO,
                subject=dataset_id,
                message="bu veri nesnesini okuyan bir süreç yok (ölü tablo olabilir)",
            )


def _has_outgoing_transfer(graph: ModelGraph, dataset_id: str) -> bool:
    return any(
        data["kind"] in {EdgeKind.TRANSFERS.value, EdgeKind.DERIVES_FROM.value}
        for _, _, data in graph.flow.out_edges(dataset_id, data=True)
    )


def rule_process_without_io(model: Model, graph: ModelGraph) -> Iterator[Finding]:
    """Süreç ne okuyor ne yazıyor — modelde boşta duruyor."""
    for process_id in model.processes:
        has_in = graph.flow.in_degree(process_id) > 0
        has_out = graph.flow.out_degree(process_id) > 0
        if not has_in and not has_out:
            yield Finding(
                rule="process_without_io",
                severity=Severity.WARNING,
                subject=process_id,
                message="sürecin girdisi de çıktısı da yok",
            )
        elif not has_out and model.processes[process_id].kind not in {
            ProcessKind.DQ_CHECK,
            ProcessKind.RECONCILIATION,
            ProcessKind.ARCHIVE_PURGE,
        }:
            yield Finding(
                rule="process_without_output",
                severity=Severity.INFO,
                subject=process_id,
                message="sürecin çıktısı modellenmemiş",
            )


def rule_layer_direction(model: Model, graph: ModelGraph) -> Iterator[Finding]:
    """Veri akışı katman sırasına ters gidiyor (ör. mart'tan staging'e)."""
    for edge in model.edges:
        if edge.kind in COLUMN_EDGES or edge.kind in CHECK_EDGES:
            continue
        source, target = model.node(edge.source_node), model.node(edge.target_node)
        if source is None or target is None:
            continue
        if LAYER_ORDER[target.layer] < LAYER_ORDER[source.layer]:
            yield Finding(
                rule="layer_direction",
                severity=Severity.WARNING,
                subject=edge.id,
                message=(
                    f"akış katman sırasına ters: {source.layer.value} → {target.layer.value}"
                ),
            )


def rule_expected_layer(model: Model, graph: ModelGraph) -> Iterator[Finding]:
    """Veri nesnesinin tipi ile katmanı uyuşmuyor."""
    for dataset_id, dataset in model.datasets.items():
        expected = EXPECTED_LAYER.get(dataset.kind)
        if expected is not None and dataset.layer is not expected:
            yield Finding(
                rule="expected_layer",
                severity=Severity.WARNING,
                subject=dataset_id,
                message=(
                    f"'{dataset.kind.value}' tipi için beklenen katman "
                    f"'{expected.value}', model '{dataset.layer.value}' diyor"
                ),
            )


# --------------------------------------------------------------------------- #
# Yürütme / zamanlama
# --------------------------------------------------------------------------- #


def rule_execution_cycles(model: Model, graph: ModelGraph) -> Iterator[Finding]:
    """Batch zincirinde döngü — bu iş hiç başlamaz."""
    for cycle in graph.execution_cycles():
        yield Finding(
            rule="execution_cycle",
            severity=Severity.ERROR,
            subject=" → ".join(cycle),
            message="yürütme sırasında döngü var, bu zincir kilitlenir",
        )


def rule_dataflow_cycles(model: Model, graph: ModelGraph) -> Iterator[Finding]:
    """Veri akışında döngü — meşru olabilir (geri besleme) ama şüphelidir."""
    for cycle in graph.dataflow_cycles():
        yield Finding(
            rule="dataflow_cycle",
            severity=Severity.WARNING,
            subject=" → ".join(cycle),
            message="veri akışında döngü var",
        )


def rule_schedule_consistency(model: Model, graph: ModelGraph) -> Iterator[Finding]:
    """Ardıl job, öncülünden önce başlıyor."""
    for edge in model.edges:
        if edge.kind is not EdgeKind.PRECEDES:
            continue
        first, second = model.node(edge.source_node), model.node(edge.target_node)
        if first is None or second is None:
            continue
        first_start = _earliest_start(first)
        second_start = _earliest_start(second)
        if first_start is not None and second_start is not None and second_start < first_start:
            yield Finding(
                rule="schedule_conflict",
                severity=Severity.WARNING,
                subject=edge.id,
                message=(
                    f"'{second.id}' ({second_start}) öncülü '{first.id}' ({first_start})'den "
                    "önce başlıyor"
                ),
            )


def _earliest_start(node: object) -> str | None:
    schedule = getattr(node, "schedule", None)
    if schedule is None or not schedule.start_times:
        return None
    return min(schedule.start_times)


def rule_sla_chain(model: Model, graph: ModelGraph) -> Iterator[Finding]:
    """SLA zinciri imkânsız: yol üzerindeki sürelerin toplamı SLA'yı aşıyor.

    Basit ama etkili bir kontrol: SLA'sı olan her sürecin en uzun yukarı akış
    zincirindeki süreleri toplar, ilk başlama saatine ekler ve SLA ile karşılaştırır.
    """
    for process_id, process in model.processes.items():
        if process.sla is None or not process.sla.target_time:
            continue
        start = _chain_start(model, graph, process_id)
        if start is None:
            continue
        total = _chain_duration(model, graph, process_id)
        if total is None:
            continue
        finish_min = _to_minutes(start) + total
        target_min = _to_minutes(process.sla.target_time)
        if target_min is None or finish_min is None:
            continue
        # Gece yarısını geçen batch pencereleri için hedefi ertesi güne taşı.
        if target_min < _to_minutes(start):
            target_min += 24 * 60
        if finish_min > target_min:
            yield Finding(
                rule="sla_chain",
                severity=Severity.WARNING,
                subject=process_id,
                message=(
                    f"zincir {start}'da başlayıp ~{total:.0f} dk sürüyor "
                    f"→ ~{_to_clock(finish_min)}, SLA {process.sla.target_time}"
                ),
            )


def _chain_start(model: Model, graph: ModelGraph, process_id: str) -> str | None:
    candidates = [process_id, *graph.upstream(process_id)]
    starts = [
        _earliest_start(model.processes[c]) for c in candidates if c in model.processes
    ]
    valid = [s for s in starts if s]
    return min(valid) if valid else None


def _chain_duration(model: Model, graph: ModelGraph, process_id: str) -> float | None:
    """Yukarı akıştaki en uzun süre zinciri (kaba kritik yol tahmini).

    Kontrol kenarları (``validates``/``reconciles``) hesaba katılmaz: tablo ile DQ
    süreci arasında ``reads`` + ``validates`` 2'li döngü kurarlar. Önceki sürüm bu
    döngüde topolojik sıralama hatasına düşüp sessizce ``None`` dönüyordu — kural
    DQ kontrolü olan her modelde hiç çalışmıyordu (inceleme #3). Kalan gerçek
    döngüler SCC'lere yoğunlaştırılır; böylece fonksiyon hiçbir zaman susmaz.
    """
    upstream = graph.upstream(process_id) | {process_id}
    allowed = {kind.value for kind in DATA_EDGES | CONTROL_EDGES}
    dag = nx.DiGraph()
    dag.add_nodes_from(upstream)
    for source, target, data in graph.flow.edges(data=True):
        if source != target and source in upstream and target in upstream and data["kind"] in allowed:
            dag.add_edge(source, target)
    condensed = nx.condensation(dag)
    mapping = condensed.graph["mapping"]

    def own(node_id: str) -> float:
        process = model.processes.get(node_id)
        if process and process.runtime and process.runtime.best_estimate_min:
            return process.runtime.best_estimate_min
        return 0.0

    weight = {scc: sum(own(n) for n in condensed.nodes[scc]["members"]) for scc in condensed.nodes}
    longest: dict[int, float] = {}
    for scc in nx.topological_sort(condensed):
        incoming = [longest[p] for p in condensed.predecessors(scc)]
        longest[scc] = weight[scc] + (max(incoming) if incoming else 0.0)
    return longest.get(mapping[process_id])


def _to_minutes(clock: str | None) -> int | None:
    if not clock or ":" not in clock:
        return None
    hours, _, minutes = clock.partition(":")
    try:
        return int(hours) * 60 + int(minutes)
    except ValueError:
        return None


def _to_clock(minutes: float) -> str:
    total = int(minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


# --------------------------------------------------------------------------- #
# Operasyonel eksikler
# --------------------------------------------------------------------------- #


def rule_missing_load_strategy(model: Model, graph: ModelGraph) -> Iterator[Finding]:
    """Tabloya yazan sürecin yükleme stratejisi belirtilmemiş."""
    loaders = {ProcessKind.ETL_MAPPING, ProcessKind.LOADER_UTILITY, ProcessKind.STORED_PROCEDURE,
               ProcessKind.SQL_SCRIPT}
    for process_id, process in model.processes.items():
        if process.kind in loaders and process.load_strategy is None:
            yield Finding(
                rule="missing_load_strategy",
                severity=Severity.INFO,
                subject=process_id,
                message="yükleme stratejisi (full/delta/merge/scd2...) belirtilmemiş",
            )


def rule_delta_without_watermark(model: Model, graph: ModelGraph) -> Iterator[Finding]:
    """Delta yükleme var ama delta'nın nasıl belirlendiği yazılmamış."""
    for process_id, process in model.processes.items():
        if process.load_strategy is None:
            continue
        if process.load_strategy.value in {"delta", "merge", "scd2"} and (
            process.delta_method is None and not process.watermark_column
        ):
            yield Finding(
                rule="delta_without_method",
                severity=Severity.INFO,
                subject=process_id,
                message=(
                    f"'{process.load_strategy.value}' yükleme ama delta yöntemi/watermark "
                    "kolonu tanımlı değil"
                ),
            )


#: Kontrol beklediğimiz katmanlar: ambar içinde bizim doldurduğumuz tablolar.
#: Kaynak sistem nesneleri, raporlar ve downstream sistemler bizim kontrol
#: noktamız değil — orada kontrol beklemek gürültü üretir.
_CHECK_EXPECTED_LAYERS = frozenset({Layer.STAGING, Layer.ODS, Layer.CORE, Layer.MART})
_CHECK_EXEMPT_KINDS = frozenset(
    {DatasetKind.CONTROL_TABLE, DatasetKind.REJECT_TABLE, DatasetKind.ARCHIVE_TABLE}
)


def rule_critical_without_check(model: Model, graph: ModelGraph) -> Iterator[Finding]:
    """Kritik veri nesnesinde DQ/mutabakat kontrolü yok (ABC çatısı, PLAN.md 3.5)."""
    for dataset_id, dataset in model.datasets.items():
        if dataset.layer not in _CHECK_EXPECTED_LAYERS or dataset.kind in _CHECK_EXEMPT_KINDS:
            continue
        if dataset.criticality >= 4 and not graph.checks_on(dataset_id):
            yield Finding(
                rule="critical_without_check",
                severity=Severity.INFO,
                subject=dataset_id,
                message=(
                    f"kritiklik {dataset.criticality} ama bağlı DQ/mutabakat süreci yok"
                ),
            )


def rule_file_without_spec(model: Model, graph: ModelGraph) -> Iterator[Finding]:
    """Dosya nesnesinin yerleşim bilgisi yok — interface belirsiz."""
    for dataset_id, dataset in model.datasets.items():
        if dataset.kind in FILE_KINDS and dataset.file_spec is None:
            yield Finding(
                rule="file_without_spec",
                severity=Severity.INFO,
                subject=dataset_id,
                message="dosya yerleşimi (format/kodlama/kayıt uzunluğu) tanımlı değil",
            )


def rule_not_restartable(model: Model, graph: ModelGraph) -> Iterator[Finding]:
    """Kritik zincirde restart edilemeyen süreç — gece operasyonunun kâbusu."""
    for process_id, process in model.processes.items():
        if process.restartable is False:
            yield Finding(
                rule="not_restartable",
                severity=Severity.INFO,
                subject=process_id,
                message="süreç restart edilemiyor; hata sonrası müdahale planı gerekir",
            )


# --------------------------------------------------------------------------- #
# Provenance / güven
# --------------------------------------------------------------------------- #


def rule_draft_items(model: Model, graph: ModelGraph) -> Iterator[Finding]:
    """Onay bekleyen taslak node/kenarlar (LLM önerileri buraya düşer)."""
    drafts = [n for n, node in model.nodes.items() if node.status is Status.DRAFT]
    draft_edges = [e.id for e in model.edges if e.status is Status.DRAFT]
    if drafts:
        yield Finding(
            rule="draft_pending",
            severity=Severity.INFO,
            subject=None,
            message=f"{len(drafts)} node onay bekliyor: {', '.join(sorted(drafts)[:5])}",
        )
    if draft_edges:
        yield Finding(
            rule="draft_pending",
            severity=Severity.INFO,
            subject=None,
            message=f"{len(draft_edges)} kenar onay bekliyor",
        )


def rule_low_confidence_edges(model: Model, graph: ModelGraph) -> Iterator[Finding]:
    """Yalnızca doküman metni ya da LLM'den gelmiş, teknik kaynakla doğrulanmamış kenarlar."""
    for edge in model.edges:
        if edge.best_source_rank >= 4 and edge.sources:
            origin = ", ".join(sorted({s.extractor for s in edge.sources}))
            yield Finding(
                rule="unverified_edge",
                severity=Severity.INFO,
                subject=edge.id,
                message=f"teknik kaynakla doğrulanmamış (yalnızca {origin})",
            )


def rule_llm_without_evidence(model: Model, graph: ModelGraph) -> Iterator[Finding]:
    """LLM çıkarımı kanıt cümlesi taşımıyor — onay ekranında gösterilemez."""
    for edge in model.edges:
        for source in edge.sources:
            if source.is_llm and not source.evidence:
                yield Finding(
                    rule="llm_without_evidence",
                    severity=Severity.WARNING,
                    subject=edge.id,
                    message=f"'{source.extractor}' çıkarımında kanıt cümlesi yok",
                )


ALL_RULES = (
    rule_unresolved_refs,
    rule_endpoint_types,
    rule_system_refs,
    rule_orphan_datasets,
    rule_dead_datasets,
    rule_process_without_io,
    rule_layer_direction,
    rule_expected_layer,
    rule_execution_cycles,
    rule_dataflow_cycles,
    rule_schedule_consistency,
    rule_sla_chain,
    rule_missing_load_strategy,
    rule_delta_without_watermark,
    rule_critical_without_check,
    rule_file_without_spec,
    rule_not_restartable,
    rule_draft_items,
    rule_low_confidence_edges,
    rule_llm_without_evidence,
)

_SEVERITY_ORDER = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}


def validate(model: Model, graph: ModelGraph | None = None) -> list[Finding]:
    """Tüm kuralları çalıştırır; şiddet sırasına göre sıralı sonuç döner."""
    graph = graph or ModelGraph(model)
    findings: list[Finding] = []
    for rule in ALL_RULES:
        findings.extend(rule(model, graph))
    return sorted(findings, key=lambda f: (_SEVERITY_ORDER[f.severity], f.rule, f.subject or ""))


def summarize(findings: list[Finding]) -> dict[str, int]:
    counts = {severity.value: 0 for severity in Severity}
    for finding in findings:
        counts[finding.severity.value] += 1
    return counts

"""Düzeltme katmanı (PLAN_V2 §3.4): elle yapılan değişiklikler işlem listesi olarak saklanır.

Model her seferinde dokümanlardan yeniden kurulur; modele doğrudan yazılan bir düzeltme
bir sonraki okumada kaybolurdu. Bu yüzden düzeltmeler **ayrı** tutulur ve okumadan
sonra modele sırayla uygulanır.

* Hedefler iç kimlikle değil **adla** verilir (büyük/küçük harf ve şema önekine
  duyarsız); yeniden okumada kimlik değişse de düzeltme tutar. Yeniden adlandırılan
  ve birleştirilen nesnelerin eski adları da çözülür.
* Hedefi bulunamayan düzeltme **sahipsiz** olur: uygulanmaz, raporlanır.
* Elle eklenen/onaylanan öğeler kaynağında ``manual:<kullanıcı>`` taşır ve onaylıdır.
* ``apply`` modeli değiştirmez; kopyası üzerinde çalışır.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..ingest.base import bare_key, is_file_name, make_id, name_key
from ..model import (
    COLUMN_SEP,
    Column,
    Dataset,
    DatasetKind,
    Edge,
    EdgeKind,
    Layer,
    Model,
    Process,
    ProcessKind,
    Sensitivity,
    SourceRef,
    Status,
)

OPS = (
    "yeniden_adlandir", "tur_degistir", "katman_degistir", "sistem_ata", "hassasiyet_ata",
    "aciklama", "birlestir", "nesne_ekle", "nesne_sil", "bag_ekle", "bag_sil", "bag_yon_cevir",
    "surec_ata", "onayla", "kolon_ekle", "kolon_esle", "bulgu_yoksay", "oneri_reddet",
)
Op = Literal[
    "yeniden_adlandir", "tur_degistir", "katman_degistir", "sistem_ata", "hassasiyet_ata",
    "aciklama", "birlestir", "nesne_ekle", "nesne_sil", "bag_ekle", "bag_sil", "bag_yon_cevir",
    "surec_ata", "onayla", "kolon_ekle", "kolon_esle", "bulgu_yoksay", "oneri_reddet",
]

OP_LABELS = {
    "yeniden_adlandir": "Yeniden adlandır", "tur_degistir": "Tür değiştir",
    "katman_degistir": "Katman değiştir", "sistem_ata": "Sistem ata",
    "hassasiyet_ata": "Hassasiyet ata", "aciklama": "Açıklama", "birlestir": "Birleştir",
    "nesne_ekle": "Nesne ekle", "nesne_sil": "Nesne sil", "bag_ekle": "Bağ ekle",
    "bag_sil": "Bağ sil", "bag_yon_cevir": "Bağ yönünü çevir", "surec_ata": "Süreç ata",
    "onayla": "Onayla", "kolon_ekle": "Kolon ekle", "kolon_esle": "Kolon eşle",
    "bulgu_yoksay": "Bulguyu yok say", "oneri_reddet": "Öneriyi reddet",
}

#: Model dışı işlemler: modele dokunmaz, boru hattında ayrıca yorumlanır.
META_OPS = {"bulgu_yoksay", "oneri_reddet"}
DIRECT_KINDS = {EdgeKind.DERIVES_FROM, EdgeKind.TRANSFERS, EdgeKind.PRECEDES}
COLUMN_KINDS = {EdgeKind.COLUMN_MAPS_TO, EdgeKind.INFLUENCES}
#: Bağ türü → (kaynak tipi, hedef tipi); ``None`` her tip olabilir demek.
EDGE_ENDPOINTS: dict[EdgeKind, tuple[str | None, str | None]] = {
    EdgeKind.READS: ("dataset", "process"),
    EdgeKind.CONSUMES_FILE: ("dataset", "process"),
    EdgeKind.WRITES: ("process", "dataset"),
    EdgeKind.PRODUCES_FILE: ("process", "dataset"),
    EdgeKind.VALIDATES: ("process", "dataset"),
    EdgeKind.RECONCILES: ("process", "dataset"),
    EdgeKind.TRANSFERS: ("dataset", "dataset"),
    EdgeKind.DERIVES_FROM: ("dataset", "dataset"),
    EdgeKind.PRECEDES: ("process", "process"),
    EdgeKind.TRIGGERS: (None, "process"),
}
NODE_TYPE_LABELS = {"dataset": "veri nesnesi (tablo/dosya)", "process": "süreç"}


class CorrectionError(ValueError):
    """Düzeltme geçersiz (HTTP 400)."""


class Correction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^d-\d{4,}$")
    islem: Op
    hedef: dict[str, Any] = Field(default_factory=dict)
    deger: dict[str, Any] = Field(default_factory=dict)
    neden: str | None = Field(default=None, max_length=500)
    kim: str
    zaman: str

    @field_validator("hedef", "deger")
    @classmethod
    def _plain(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key, item in value.items():
            if not isinstance(item, (str, int, float, bool, list, type(None))):
                raise ValueError(f"'{key}' değeri düz bir değer olmalı")
        return value


# --------------------------------------------------------------------------- #
# Ad çözümleme
# --------------------------------------------------------------------------- #


class NameIndex:
    """Ad → node kimliği. Eski adlar (``attrs.eski_adlar``) ve birleşen adlar da çözülür."""

    def __init__(self, model: Model) -> None:
        self.exact: dict[str, set[str]] = {}
        self.bare: dict[str, set[str]] = {}
        for node_id, node in model.nodes.items():
            names = [node.name, node_id, *node.attrs.get("eski_adlar", []), *node.attrs.get("birlesen_adlar", [])]
            for raw in names:
                key = name_key(raw)
                self.exact.setdefault(key, set()).add(node_id)
                self.bare.setdefault(bare_key(key), set()).add(node_id)

    def resolve(self, raw: str | None) -> str | None:
        if not raw:
            return None
        key = name_key(str(raw))
        hits = self.exact.get(key) or set()
        if len(hits) == 1:
            return next(iter(hits))
        if not hits and not is_file_name(key):
            hits = self.bare.get(bare_key(key)) or set()
            if len(hits) == 1:
                return next(iter(hits))
        return None

    def ambiguous(self, raw: str) -> bool:
        key = name_key(str(raw))
        return len(self.exact.get(key, ())) > 1 or (
            not self.exact.get(key) and len(self.bare.get(bare_key(key), ())) > 1)


def split_column_ref(raw: str) -> tuple[str, str | None]:
    """``"DWH.F_X#TUTAR"`` → (``"DWH.F_X"``, ``"TUTAR"``)."""
    table, sep, column = str(raw).rpartition(COLUMN_SEP)
    return (table, column.strip().upper() or None) if sep else (str(raw), None)


# --------------------------------------------------------------------------- #
# Uygulama
# --------------------------------------------------------------------------- #


@dataclass
class ApplyReport:
    results: list[dict[str, Any]] = field(default_factory=list)

    def add(self, op: Correction, state: str, message: str = "") -> None:
        self.results.append({"id": op.id, "islem": op.islem, "durum": state, "mesaj": message})

    @property
    def orphans(self) -> list[dict[str, Any]]:
        return [r for r in self.results if r["durum"] == "sahipsiz"]

    def by_id(self) -> dict[str, dict[str, Any]]:
        return {r["id"]: r for r in self.results}


class _Orphan(Exception):
    """Hedef bulunamadı."""


def _manual_source(op: Correction) -> SourceRef:
    return SourceRef(extractor=f"manual:{op.kim}", locator=op.id, evidence=op.neden, confidence=1.0)


def _enum(enum: type, value: Any, label: str):
    try:
        return enum(str(value))
    except ValueError as exc:
        allowed = ", ".join(e.value for e in enum)
        raise CorrectionError(f"geçersiz {label}: {value!r} (geçerli: {allowed})") from exc


class _Applier:
    def __init__(self, model: Model) -> None:
        self.model = model
        self._index: NameIndex | None = None

    @property
    def index(self) -> NameIndex:
        if self._index is None:
            self._index = NameIndex(self.model)
        return self._index

    def dirty(self) -> None:
        self._index = None

    def node(self, raw: Any, what: str = "nesne") -> str:
        node_id = self.index.resolve(raw)
        if node_id is None:
            if raw and self.index.ambiguous(str(raw)):
                raise _Orphan(f"'{raw}' birden fazla nesneyle eşleşiyor")
            raise _Orphan(f"{what} bulunamadı: {raw!r}")
        return node_id

    def ref(self, raw: Any) -> str:
        """Nesne ya da ``TABLO#KOLON`` başvurusu → model başvurusu.

        Adında ``#`` geçen nesne (ör. ``"kart_yukleme #1"`` süreci) önce bütün adıyla aranır;
        yalnız öyle bir nesne yoksa son ``#``ten tablo ve kolon diye bölünür."""
        text = "" if raw is None else str(raw)
        if COLUMN_SEP in text:
            whole = self.index.resolve(text)
            if whole is not None:
                return whole
        table, column = split_column_ref(text)
        node_id = self.node(table)
        if column is None:
            return node_id
        dataset = self.model.datasets.get(node_id)
        if dataset is None:
            raise _Orphan(f"kolon yalnız veri nesnesinde olabilir: {raw!r}")
        return f"{node_id}{COLUMN_SEP}{column}"

    # -- yardımcılar ---------------------------------------------------------- #

    def _add_source(self, item, op: Correction) -> None:
        item.sources = [*item.sources, _manual_source(op)]
        item.status = Status.CONFIRMED

    def _ensure_column(self, ref: str, data_type: str | None = None) -> None:
        node_id, _, column = ref.partition(COLUMN_SEP)
        dataset = self.model.datasets[node_id]
        if dataset.column(column) is None:
            dataset.columns = [*dataset.columns, Column(name=column, data_type=data_type)]

    def _edges_between(self, source: str, target: str, kind: EdgeKind | None) -> list[Edge]:
        return [e for e in self.model.edges if e.source == source and e.target == target
                and (kind is None or e.kind is kind)]

    def _default_kind(self, source: str, target: str) -> EdgeKind:
        if COLUMN_SEP in source or COLUMN_SEP in target:
            return EdgeKind.COLUMN_MAPS_TO
        src_type, tgt_type = self.model.node_type_of(source), self.model.node_type_of(target)
        return {
            ("dataset", "dataset"): EdgeKind.DERIVES_FROM,
            ("dataset", "process"): EdgeKind.READS,
            ("process", "dataset"): EdgeKind.WRITES,
            ("process", "process"): EdgeKind.PRECEDES,
        }[(src_type, tgt_type)]

    def _add_edge(self, op: Correction, kind: EdgeKind, source: str, target: str,
                  expression: str | None = None) -> str:
        if source == target:
            raise CorrectionError("bir nesne kendisine bağlanamaz")
        existing = self._edges_between(source, target, kind)
        if existing:
            for edge in existing:
                self._add_source(edge, op)
                if expression and not edge.expression:
                    edge.expression = expression
            return "mevcut bağ onaylandı"
        self.model.edges.append(Edge(kind=kind, source=source, target=target, expression=expression,
                                     status=Status.CONFIRMED, sources=[_manual_source(op)]))
        return "bağ eklendi"

    def _process(self, op: Correction, name: str) -> str:
        node_id = self.index.resolve(name)
        if node_id is not None:
            if node_id not in self.model.processes:
                raise CorrectionError(f"'{name}' bir süreç değil")
            return node_id
        node_id = make_id(name, set(self.model.nodes))
        self.model.add(Process(id=node_id, name=name.strip(), kind=ProcessKind.ETL_MAPPING,
                               layer=Layer.STAGING, sources=[_manual_source(op)]))
        self.dirty()
        return node_id

    # -- işlemler ----------------------------------------------------------- #

    def apply(self, op: Correction) -> str:
        handler = getattr(self, f"op_{op.islem}")
        message = handler(op)
        return message or ""

    def op_yeniden_adlandir(self, op: Correction) -> str:
        node_id = self.node(op.hedef.get("nesne"))
        new = str(op.deger.get("ad") or "").strip()
        if not new:
            raise CorrectionError("yeni ad boş olamaz")
        other = self.index.resolve(new)
        if other is not None and other != node_id:
            raise _Orphan(f"'{new}' adı başka bir nesnede kullanılıyor")
        node = self.model.node(node_id)
        node.attrs = {**node.attrs, "eski_adlar": [*node.attrs.get("eski_adlar", []), node.name]}
        node.name = new
        self.dirty()
        return f"{node_id}: {new}"

    def op_tur_degistir(self, op: Correction) -> str:
        node_id = self.node(op.hedef.get("nesne"))
        value = op.deger.get("tur")
        if node_id in self.model.datasets:
            self.model.datasets[node_id].kind = _enum(DatasetKind, value, "veri nesnesi türü")
        else:
            self.model.processes[node_id].kind = _enum(ProcessKind, value, "süreç türü")
        return ""

    def op_katman_degistir(self, op: Correction) -> str:
        node_id = self.node(op.hedef.get("nesne"))
        self.model.node(node_id).layer = _enum(Layer, op.deger.get("katman"), "katman")
        return ""

    def op_sistem_ata(self, op: Correction) -> str:
        node_id = self.node(op.hedef.get("nesne"))
        value = op.deger.get("sistem")
        self.model.node(node_id).system = str(value).strip() if value else None
        return ""

    def op_hassasiyet_ata(self, op: Correction) -> str:
        node_id = self.node(op.hedef.get("nesne"))
        if node_id not in self.model.datasets:
            raise CorrectionError("hassasiyet yalnız veri nesnelerine atanır")
        self.model.datasets[node_id].sensitivity = _enum(Sensitivity, op.deger.get("hassasiyet"), "hassasiyet")
        return ""

    def op_aciklama(self, op: Correction) -> str:
        node_id = self.node(op.hedef.get("nesne"))
        value = op.deger.get("aciklama")
        self.model.node(node_id).description = str(value).strip() if value else None
        return ""

    def op_birlestir(self, op: Correction) -> str:
        raw = op.hedef.get("nesneler") or []
        if not isinstance(raw, list) or len(raw) < 2:
            raise CorrectionError("birleştirmek için en az iki nesne gerekir")
        ids = list(dict.fromkeys(self.node(name) for name in raw))
        if len(ids) < 2:
            return "nesneler zaten tek"
        kinds = {self.model.node_type_of(node_id) for node_id in ids}
        if len(kinds) > 1:
            raise CorrectionError("veri nesnesi ile süreç birleştirilemez")
        final_name = str(op.deger.get("ad") or "").strip()
        keep = next((i for i in ids if final_name and name_key(self.model.node(i).name) == name_key(final_name)),
                    ids[0])
        others = [i for i in ids if i != keep]
        survivor = self.model.node(keep)
        merged_names = list(survivor.attrs.get("birlesen_adlar", []))
        for other_id in others:
            other = self.model.node(other_id)
            merged_names += [other.name, *other.attrs.get("eski_adlar", []), *other.attrs.get("birlesen_adlar", [])]
            survivor.sources = [*survivor.sources, *other.sources][:30]
            survivor.system = survivor.system or other.system
            survivor.description = survivor.description or other.description
            if other.status is Status.CONFIRMED:
                survivor.status = Status.CONFIRMED
            if isinstance(survivor, Dataset) and isinstance(other, Dataset):
                known = {c.name for c in survivor.columns}
                survivor.columns = [*survivor.columns, *(c for c in other.columns if c.name not in known)]
        survivor.attrs = {**survivor.attrs, "birlesen_adlar": list(dict.fromkeys(merged_names))}
        if final_name and name_key(survivor.name) != name_key(final_name):
            survivor.attrs = {**survivor.attrs,
                              "eski_adlar": [*survivor.attrs.get("eski_adlar", []), survivor.name]}
            survivor.name = final_name
        self._redirect(others, keep)
        for other_id in others:
            self.model.datasets.pop(other_id, None)
            self.model.processes.pop(other_id, None)
        survivor.sources = [*survivor.sources, _manual_source(op)]
        self.dirty()
        return f"{len(others)} nesne {keep} içinde birleşti"

    def _redirect(self, old_ids: list[str], new_id: str) -> None:
        def move(ref: str) -> str:
            node_id, sep, column = ref.partition(COLUMN_SEP)
            return f"{new_id}{sep}{column}" if node_id in old_ids else ref

        edges: dict[str, Edge] = {}
        for edge in self.model.edges:
            source, target = move(edge.source), move(edge.target)
            if source == target:
                continue
            if (source, target) != (edge.source, edge.target):
                edge = Edge(**{**edge.model_dump(), "source": source, "target": target, "id": None})
            if edge.id in edges:
                kept = edges[edge.id]
                kept.sources = [*kept.sources, *edge.sources][:30]
                if edge.status is Status.CONFIRMED:
                    kept.status = Status.CONFIRMED
            else:
                edges[edge.id] = edge
        self.model.edges = list(edges.values())

    def op_nesne_ekle(self, op: Correction) -> str:
        name = str(op.hedef.get("ad") or "").strip()
        node_type = op.hedef.get("tip", "dataset")
        if not name:
            raise CorrectionError("nesne adı boş olamaz")
        if node_type not in ("dataset", "process"):
            raise CorrectionError("tip 'dataset' ya da 'process' olmalı")
        existing = self.index.resolve(name)
        if existing is not None:
            node = self.model.node(existing)
            if self.model.node_type_of(existing) != node_type:
                raise _Orphan(f"'{name}' adında başka türde bir nesne var")
            self._apply_fields(existing, op.deger)
            self._add_source(node, op)
            return "mevcut nesne onaylandı"
        node_id = make_id(name, set(self.model.nodes))
        layer = _enum(Layer, op.deger["katman"], "katman") if op.deger.get("katman") else None
        if node_type == "dataset":
            from ..ingest.base import explain_name

            guess = explain_name(name)
            layer = layer or guess["layer"] or Layer.CORE
            kind = (_enum(DatasetKind, op.deger["tur"], "veri nesnesi türü") if op.deger.get("tur")
                    else guess["kind"] or _default_dataset_kind(layer))
            self.model.add(Dataset(id=node_id, name=name, kind=kind, layer=layer,
                                   system=op.deger.get("sistem") or None,
                                   description=op.deger.get("aciklama") or None,
                                   sources=[_manual_source(op)]))
        else:
            kind = (_enum(ProcessKind, op.deger["tur"], "süreç türü") if op.deger.get("tur")
                    else ProcessKind.ETL_MAPPING)
            self.model.add(Process(id=node_id, name=name, kind=kind, layer=layer or Layer.STAGING,
                                   system=op.deger.get("sistem") or None,
                                   description=op.deger.get("aciklama") or None,
                                   sources=[_manual_source(op)]))
        self.dirty()
        return f"{node_id} eklendi"

    def _apply_fields(self, node_id: str, values: dict[str, Any]) -> None:
        node = self.model.node(node_id)
        enum = DatasetKind if node_id in self.model.datasets else ProcessKind
        layer = _enum(Layer, values["katman"], "katman") if values.get("katman") else None
        kind = _enum(enum, values["tur"], "tür") if values.get("tur") else None
        if layer is not None:
            node.layer = layer
        if kind is not None:
            node.kind = kind
        if values.get("sistem"):
            node.system = str(values["sistem"])
        if values.get("aciklama"):
            node.description = str(values["aciklama"])

    def op_nesne_sil(self, op: Correction) -> str:
        node_id = self.node(op.hedef.get("nesne"))
        self.model.datasets.pop(node_id, None)
        self.model.processes.pop(node_id, None)
        self.model.edges = [e for e in self.model.edges if node_id not in (e.source_node, e.target_node)]
        self.dirty()
        return f"{node_id} silindi"

    def _kind(self, op: Correction, source: str, target: str) -> EdgeKind:
        raw = op.hedef.get("tur")
        kind = _enum(EdgeKind, raw, "bağ türü") if raw else self._default_kind(source, target)
        self._check_endpoints(kind, source, target)
        return kind

    def _check_endpoints(self, kind: EdgeKind, source: str, target: str) -> None:
        """Bağ türünün iki ucu doğru türde mi (ör. ``reads`` veri nesnesinden sürece gider)."""
        columns = (COLUMN_SEP in source, COLUMN_SEP in target)
        if kind in COLUMN_KINDS:
            if not all(columns):
                raise CorrectionError("kolon bağının iki ucu da TABLO#KOLON olmalı")
            return
        if any(columns):
            raise CorrectionError(f"'{kind.value}' bağı kolonlar arasında kurulamaz")
        expected = EDGE_ENDPOINTS.get(kind, (None, None))
        actual = (self.model.node_type_of(source), self.model.node_type_of(target))
        for end, want, got in zip(("kaynak", "hedef"), expected, actual, strict=True):
            if want and want != got:
                raise CorrectionError(f"'{kind.value}' bağında {end} bir {NODE_TYPE_LABELS[want]} olmalı")

    def op_bag_ekle(self, op: Correction) -> str:
        source, target = self.ref(op.hedef.get("kaynak")), self.ref(op.hedef.get("hedef"))
        process = op.deger.get("surec")
        expression = op.deger.get("ifade") or None
        if process:
            if COLUMN_SEP in source or COLUMN_SEP in target:
                raise CorrectionError("kolon eşlemesine süreç atanamaz")
            if source not in self.model.datasets or target not in self.model.datasets:
                raise CorrectionError("süreçli bağ iki veri nesnesi arasında olmalı")
            existing = self.index.resolve(str(process))
            if existing is not None and existing not in self.model.processes:
                raise CorrectionError(f"'{process}' bir süreç değil")
            process_id = self._process(op, str(process))
            first = self._add_edge(op, EdgeKind.READS, source, process_id)
            self._add_edge(op, EdgeKind.WRITES, process_id, target)
            return first
        kind = self._kind(op, source, target)
        if kind in COLUMN_KINDS:
            for ref in (source, target):
                self._ensure_column(ref)
        return self._add_edge(op, kind, source, target, expression)

    def op_kolon_esle(self, op: Correction) -> str:
        source, target = self.ref(op.hedef.get("kaynak")), self.ref(op.hedef.get("hedef"))
        if COLUMN_SEP not in source or COLUMN_SEP not in target:
            raise CorrectionError("kolon eşlemesinin iki ucu da TABLO#KOLON olmalı")
        self._ensure_column(source)
        self._ensure_column(target)
        return self._add_edge(op, EdgeKind.COLUMN_MAPS_TO, source, target, op.deger.get("ifade") or None)

    def op_kolon_ekle(self, op: Correction) -> str:
        node_id = self.node(op.hedef.get("nesne"))
        if node_id not in self.model.datasets:
            raise CorrectionError("kolon yalnız veri nesnesine eklenir")
        name = str(op.deger.get("ad") or "").strip().upper()
        if not name:
            raise CorrectionError("kolon adı boş olamaz")
        dataset = self.model.datasets[node_id]
        existing = dataset.column(name)
        if existing is None:
            dataset.columns = [*dataset.columns, Column(name=name, data_type=op.deger.get("tip") or None,
                                                        pk=bool(op.deger.get("pk")))]
            return "kolon eklendi"
        if op.deger.get("tip"):
            existing.data_type = str(op.deger["tip"])
        if "pk" in op.deger:
            existing.pk = bool(op.deger["pk"])
        return "kolon güncellendi"

    def op_bag_sil(self, op: Correction) -> str:
        source, target = self.ref(op.hedef.get("kaynak")), self.ref(op.hedef.get("hedef"))
        raw = op.hedef.get("tur")
        kind = _enum(EdgeKind, raw, "bağ türü") if raw else None
        before = len(self.model.edges)
        self.model.edges = [e for e in self.model.edges
                            if not (e.source == source and e.target == target and (kind is None or e.kind is kind))]
        removed = before - len(self.model.edges)
        if not removed:
            raise _Orphan("silinecek bağ bulunamadı")
        return f"{removed} bağ silindi"

    def op_bag_yon_cevir(self, op: Correction) -> str:
        source, target = self.ref(op.hedef.get("kaynak")), self.ref(op.hedef.get("hedef"))
        raw = op.hedef.get("tur")
        kind = _enum(EdgeKind, raw, "bağ türü") if raw else None
        matches = [e for e in self._edges_between(source, target, kind) if e.kind in DIRECT_KINDS]
        if not matches:
            raise _Orphan("yönü çevrilecek doğrudan bağ bulunamadı (okur/yazar bağları çevrilemez)")
        for edge in matches:
            self.model.edges.remove(edge)
            flipped = Edge(**{**edge.model_dump(), "source": target, "target": source, "id": None})
            flipped.sources = [*flipped.sources, _manual_source(op)]
            flipped.status = Status.CONFIRMED
            if not self._edges_between(target, source, edge.kind):
                self.model.edges.append(flipped)
        return f"{len(matches)} bağ çevrildi"

    def op_surec_ata(self, op: Correction) -> str:
        source, target = self.node(op.hedef.get("kaynak")), self.node(op.hedef.get("hedef"))
        if source not in self.model.datasets or target not in self.model.datasets:
            raise CorrectionError("süreç iki veri nesnesi arasındaki bağa atanır")
        process = str(op.deger.get("surec") or "").strip()
        if not process:
            raise CorrectionError("süreç adı boş olamaz")
        existing = self.index.resolve(process)
        if existing is not None and existing not in self.model.processes:
            raise CorrectionError(f"'{process}' bir süreç değil")
        direct = [e for e in self._edges_between(source, target, None)
                  if e.kind in (EdgeKind.DERIVES_FROM, EdgeKind.TRANSFERS)]
        for edge in direct:
            self.model.edges.remove(edge)
        process_id = self._process(op, process)
        self._add_edge(op, EdgeKind.READS, source, process_id)
        self._add_edge(op, EdgeKind.WRITES, process_id, target)
        return f"{len(direct)} doğrudan bağ süreçle değiştirildi"

    def op_onayla(self, op: Correction) -> str:
        if op.hedef.get("nesne"):
            node_id = self.node(op.hedef["nesne"])
            self._add_source(self.model.node(node_id), op)
            return ""
        source, target = self.ref(op.hedef.get("kaynak")), self.ref(op.hedef.get("hedef"))
        raw = op.hedef.get("tur")
        edges = self._edges_between(source, target, _enum(EdgeKind, raw, "bağ türü") if raw else None)
        if not edges:
            raise _Orphan("onaylanacak bağ bulunamadı")
        for edge in edges:
            self._add_source(edge, op)
        return f"{len(edges)} bağ onaylandı"

    def op_bulgu_yoksay(self, op: Correction) -> str:
        return ""

    def op_oneri_reddet(self, op: Correction) -> str:
        return ""


def _default_dataset_kind(layer: Layer) -> DatasetKind:
    return {
        Layer.SOURCE: DatasetKind.SOURCE_OBJECT, Layer.INTERFACE: DatasetKind.INTERFACE_FILE,
        Layer.STAGING: DatasetKind.STAGING_TABLE, Layer.ODS: DatasetKind.ODS_TABLE,
        Layer.CORE: DatasetKind.CORE_TABLE, Layer.MART: DatasetKind.MART_TABLE,
        Layer.CONSUMPTION: DatasetKind.REPORT,
    }[layer]


def parse(items: list[dict[str, Any]]) -> list[Correction]:
    out = []
    for index, item in enumerate(items or []):
        try:
            out.append(Correction.model_validate(item))
        except ValidationError as exc:
            raise CorrectionError(f"duzeltmeler.yaml öğe {index + 1}: {exc.errors()[0]['msg']}") from exc
    return out


def apply(model: Model, corrections: list[Correction]) -> tuple[Model, ApplyReport]:
    """Düzeltmeleri modelin kopyasına sırayla uygular."""
    result = model.model_copy(deep=True)
    applier = _Applier(result)
    report = ApplyReport()
    # Her işlem önce hedeflerini çözer ve değerlerini doğrular, ancak ondan sonra modeli
    # değiştirir; bu yüzden reddedilen bir işlem modelde yarım iz bırakmaz.
    for op in corrections:
        if op.islem in META_OPS:
            report.add(op, "uygulandi")
            continue
        try:
            message = applier.apply(op)
            report.add(op, "uygulandi", message)
        except _Orphan as exc:
            report.add(op, "sahipsiz", str(exc))
        except (CorrectionError, ValueError) as exc:
            report.add(op, "hata", str(exc))
        finally:
            applier.dirty()
    return result, report


def check(model: Model, op: Correction) -> str:
    """Yeni düzeltmeyi kaydetmeden önce dener: hedef şu anki modelde yoksa ya da işlem
    geçersizse ``CorrectionError`` fırlatır, değilse uygulama mesajını döner."""
    _, report = apply(model, [op])
    result = report.results[0]
    if result["durum"] != "uygulandi":
        raise CorrectionError(result["mesaj"])
    return result["mesaj"]


def ignored_findings(corrections: list[Correction]) -> set[tuple[str, str]]:
    return {(str(op.hedef.get("kural")), str(op.hedef.get("ozne") or "")) for op in corrections
            if op.islem == "bulgu_yoksay"}


def rejected_suggestions(corrections: list[Correction]) -> set[str]:
    return {str(op.hedef.get("anahtar")) for op in corrections if op.islem == "oneri_reddet"}


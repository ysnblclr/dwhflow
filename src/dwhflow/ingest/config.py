"""Okuma yapılandırması: arayüzden değiştirilebilen okuma kuralları.

Okuyucular modül sabitleri yerine **etkin yapılandırmayı** kullanır:

* STTM sütun eş anlamlıları (rol → başlık listesi),
* ad → katman/tür kuralları (önek, sonek, düzenli ifade, tam ad),
* kaynak sistem sözlüğü (``CB`` = ``Core Banking``),
* dosya başına okuyucu zorlama ve kuruma özel okuma şablonları.

Etkin yapılandırma bir ``ContextVar``'dadır; ``ingest_files`` okuma boyunca
``using(config)`` ile kurar. Yapılandırma verilmezse ``DEFAULT`` kullanılır ve
davranış ayarların eklenmesinden önceki sürümle birebir aynıdır.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from ..model import DatasetKind, Layer

# --------------------------------------------------------------------------- #
# Yerleşik varsayılanlar
# --------------------------------------------------------------------------- #

#: Rol → başlık eş anlamlıları (``fold`` edilmiş biçimde).
DEFAULT_ROLES: dict[str, tuple[str, ...]] = {
    "target_table": ("hedef tablo", "target table", "tgt table", "hedef nesne", "target object",
                     "hedef", "target", "tgt", "hedef dosya", "target file", "yazilan tablo",
                     "to table", "to"),
    "target_column": ("hedef kolon", "hedef alan", "hedef sutun", "target column", "target field",
                      "tgt column", "tgt col", "hedef kolon adi"),
    "source_table": ("kaynak tablo", "source table", "src table", "kaynak nesne", "source object",
                     "kaynak", "source", "src", "kaynak dosya", "source file", "okunan tablo",
                     "from table", "from"),
    "source_column": ("kaynak kolon", "kaynak alan", "kaynak sutun", "source column", "source field",
                      "src column", "src col"),
    "transform": ("donusum", "donusum kurali", "transformation", "transform", "kural", "rule",
                  "is kurali", "business rule", "logic", "mantik", "expression", "ifade",
                  "mapping rule", "hesaplama", "aciklama", "description"),
    "job": ("job", "job adi", "is", "is adi", "process", "surec", "mapping", "mapping adi",
            "etl", "etl job", "session", "workflow", "program", "script", "prosedur",
            "procedure", "adim", "step", "task", "gorev"),
    "predecessor": ("onceki is", "onceki job", "bagimlilik", "depends on", "dependency",
                    "predecessor", "after", "sonra", "onkosul", "condition", "kosul", "bekler"),
    "load": ("yukleme", "yukleme tipi", "yukleme stratejisi", "load type", "load strategy",
             "load", "strategy", "yukleme yontemi", "method"),
    "tool": ("arac", "tool", "teknoloji", "technology", "platform"),
    "source_system": ("kaynak sistem", "source system", "src system"),
    "target_system": ("hedef sistem", "target system", "tgt system"),
    "schedule": ("zaman", "saat", "baslangic", "baslangic saati", "schedule", "start time",
                 "calisma saati", "periyot", "frequency", "siklik"),
    "data_type": ("tip", "veri tipi", "data type", "type", "format"),
}

#: Arayüzde gösterilecek rol adları.
ROLE_LABELS: dict[str, str] = {
    "target_table": "Hedef tablo", "target_column": "Hedef kolon",
    "source_table": "Kaynak tablo", "source_column": "Kaynak kolon",
    "transform": "Dönüşüm kuralı", "job": "Job / süreç", "predecessor": "Önceki job",
    "load": "Yükleme stratejisi", "tool": "Araç", "source_system": "Kaynak sistem",
    "target_system": "Hedef sistem", "schedule": "Zamanlama", "data_type": "Veri tipi",
}

_DEFAULT_PREFIXES: list[tuple[tuple[str, ...], Layer, DatasetKind]] = [
    (("SRC_", "SOURCE_", "KAYNAK_"), Layer.SOURCE, DatasetKind.SOURCE_OBJECT),
    (("LND_", "LANDING_", "RAW_"), Layer.INTERFACE, DatasetKind.LANDING_FILE),
    (("IF_", "INTF_", "IFC_"), Layer.INTERFACE, DatasetKind.INTERFACE_FILE),
    (("STG_", "STAGE_", "STAGING_", "ST_", "TMP_", "WRK_"), Layer.STAGING, DatasetKind.STAGING_TABLE),
    (("CTL_", "CTRL_", "CONTROL_"), Layer.STAGING, DatasetKind.CONTROL_TABLE),
    (("REJ_", "ERR_", "REJECT_", "HATA_"), Layer.STAGING, DatasetKind.REJECT_TABLE),
    (("ODS_",), Layer.ODS, DatasetKind.ODS_TABLE),
    (("CORE_", "DWH_", "EDW_", "HUB_", "LNK_", "LINK_", "SAT_", "HIST_", "TRH_"),
     Layer.CORE, DatasetKind.CORE_TABLE),
    (("DIM_", "D_", "DM_DIM_"), Layer.MART, DatasetKind.DIMENSION_TABLE),
    (("FACT_", "F_", "FCT_", "FT_"), Layer.MART, DatasetKind.FACT_TABLE),
    (("AGG_", "SUM_", "OZET_"), Layer.MART, DatasetKind.AGGREGATE_TABLE),
    (("MART_", "DM_", "DMRT_"), Layer.MART, DatasetKind.MART_TABLE),
    (("RPT_", "REPORT_", "RAPOR_", "DASH_", "PANO_"), Layer.CONSUMPTION, DatasetKind.REPORT),
    (("EXP_", "EXPORT_", "OUT_", "CIKIS_"), Layer.CONSUMPTION, DatasetKind.EXPORT_FILE),
    (("ARC_", "ARCH_", "ARSIV_"), Layer.CORE, DatasetKind.ARCHIVE_TABLE),
]

RULE_TYPES = ("onek", "sonek", "regex", "tam")


@dataclass(frozen=True)
class NameRule:
    """Ad → katman/tür kuralı. ``value`` karşılaştırması büyük harfle yapılır."""

    type: str  # onek | sonek | regex | tam
    value: str
    layer: Layer
    kind: DatasetKind | None = None
    origin: str = "varsayilan"  # varsayilan | ayar

    def __post_init__(self) -> None:
        if self.type not in RULE_TYPES:
            raise ValueError(f"bilinmeyen kural türü: {self.type}")
        if self.type == "regex":
            re.compile(self.value, re.IGNORECASE)  # geçersiz ifade erken hata versin

    def matches(self, bare_upper: str) -> bool:
        value = self.value.upper()
        if self.type == "onek":
            return bare_upper.startswith(value)
        if self.type == "sonek":
            return bare_upper.endswith(value)
        if self.type == "tam":
            return bare_upper == value
        return re.search(self.value, bare_upper, re.IGNORECASE) is not None

    def describe(self) -> str:
        labels = {"onek": "önek", "sonek": "sonek", "regex": "düzenli ifade", "tam": "tam ad"}
        return f"{labels[self.type]} '{self.value}'"


def default_rules() -> list[NameRule]:
    return [NameRule("onek", prefix, layer, kind)
            for prefixes, layer, kind in _DEFAULT_PREFIXES for prefix in prefixes]


# --------------------------------------------------------------------------- #
# Yapılandırma
# --------------------------------------------------------------------------- #


def _fold(text: str) -> str:
    # base.fold ile aynı; döngüsel içe aktarmayı önlemek için burada tekrarlanır.
    import unicodedata

    table = str.maketrans("çğıöşüÇĞİÖŞÜâîû", "cgiosuCGIOSUaiu")
    value = unicodedata.normalize("NFKD", str(text).translate(table))
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


@dataclass(frozen=True)
class IngestConfig:
    roles: dict[str, tuple[str, ...]] = field(default_factory=lambda: dict(DEFAULT_ROLES))
    rules: tuple[NameRule, ...] = field(default_factory=lambda: tuple(default_rules()))
    #: fold(eş anlamlı) → kanonik sistem adı
    systems: dict[str, str] = field(default_factory=dict)
    #: dosya adı → uzantı (ör. ``{"notlar.txt": ".jil"}``)
    forced_readers: dict[str, str] = field(default_factory=dict)
    #: kuruma özel okuma şablonları (ham sözlükler; ``formats`` okuyucuları yorumlar)
    templates: tuple[dict[str, Any], ...] = ()

    # -- kurulum ------------------------------------------------------------ #

    @classmethod
    def from_settings(cls, reading: dict[str, Any] | None) -> IngestConfig:
        """Ayarların ``okuma`` bölümünden yapılandırma kurar (bkz. ``project.settings``)."""
        reading = reading or {}
        roles = {role: tuple(synonyms) for role, synonyms in DEFAULT_ROLES.items()}
        for role, extra in (reading.get("es_anlamlilar") or {}).items():
            if role not in roles:
                raise ValueError(f"bilinmeyen sütun rolü: {role}")
            added = tuple(dict.fromkeys(_fold(s) for s in extra if _fold(s)))
            roles[role] = tuple(dict.fromkeys(added + roles[role]))
        custom = [
            NameRule(r["tur"], r["deger"], Layer(r["katman"]),
                     DatasetKind(r["nesne_turu"]) if r.get("nesne_turu") else None, "ayar")
            for r in (reading.get("ad_kurallari") or [])
        ]
        rules = custom + (default_rules() if reading.get("varsayilan_ad_kurallari", True) else [])
        systems: dict[str, str] = {}
        for canonical, aliases in (reading.get("sistemler") or {}).items():
            for alias in [canonical, *(aliases or [])]:
                if _fold(alias):
                    systems[_fold(alias)] = canonical
        return cls(
            roles=roles, rules=tuple(rules), systems=systems,
            forced_readers={k: str(v).lower() for k, v in (reading.get("okuyucu_zorla") or {}).items()},
            templates=tuple(reading.get("sablonlar") or ()),
        )

    # -- sorgular ----------------------------------------------------------- #

    def system_name(self, raw: str | None) -> str | None:
        """Sistem adını sözlükteki kanonik adına çevirir; sözlükte yoksa olduğu gibi."""
        if not raw:
            return raw
        return self.systems.get(_fold(raw), raw)

    def match_rule(self, bare_upper: str) -> NameRule | None:
        return next((rule for rule in self.rules if rule.matches(bare_upper)), None)

    def fingerprint(self) -> str:
        """Önbellek anahtarı: okuma sonucunu etkileyen her şeyin özeti."""
        payload = {
            "roles": self.roles,
            "rules": [(r.type, r.value, r.layer.value, r.kind.value if r.kind else None)
                      for r in self.rules],
            "systems": self.systems, "forced": self.forced_readers,
            "templates": list(self.templates),
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


DEFAULT = IngestConfig()
_ACTIVE: ContextVar[IngestConfig] = ContextVar("dwhflow_ingest_config", default=DEFAULT)


def active() -> IngestConfig:
    return _ACTIVE.get()


@contextmanager
def using(config: IngestConfig | None) -> Iterator[IngestConfig]:
    token = _ACTIVE.set(config or DEFAULT)
    try:
        yield _ACTIVE.get()
    finally:
        _ACTIVE.reset(token)

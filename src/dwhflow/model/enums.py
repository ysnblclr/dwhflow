"""Kanonik modelin sabit değer kümeleri (PLAN.md Bölüm 3)."""

from __future__ import annotations

from enum import Enum


class Layer(str, Enum):
    """Veri ambarı katmanları. Sıralama akış yönünü belirler (bkz. LAYER_ORDER)."""

    SOURCE = "source"
    INTERFACE = "interface"
    STAGING = "staging"
    ODS = "ods"
    CORE = "core"
    MART = "mart"
    CONSUMPTION = "consumption"


LAYER_ORDER: dict[Layer, int] = {layer: i for i, layer in enumerate(Layer)}


class NodeType(str, Enum):
    DATASET = "dataset"
    PROCESS = "process"


class DatasetKind(str, Enum):
    SOURCE_OBJECT = "source_object"
    INTERFACE_FILE = "interface_file"
    LANDING_FILE = "landing_file"
    STAGING_TABLE = "staging_table"
    ODS_TABLE = "ods_table"
    CORE_TABLE = "core_table"
    DIMENSION_TABLE = "dimension_table"
    FACT_TABLE = "fact_table"
    AGGREGATE_TABLE = "aggregate_table"
    MART_TABLE = "mart_table"
    EXPORT_FILE = "export_file"
    REPORT = "report"
    DOWNSTREAM_SYSTEM = "downstream_system"
    CONTROL_TABLE = "control_table"
    REJECT_TABLE = "reject_table"
    ARCHIVE_TABLE = "archive_table"


#: Her veri nesnesi tipinin beklenen katmanı. Doğrulama uyarı üretir, hata değil:
#: gerçek kurulumlarda istisnalar olur (ör. mart'ta duran bir kontrol tablosu).
EXPECTED_LAYER: dict[DatasetKind, Layer] = {
    DatasetKind.SOURCE_OBJECT: Layer.SOURCE,
    DatasetKind.INTERFACE_FILE: Layer.INTERFACE,
    DatasetKind.LANDING_FILE: Layer.INTERFACE,
    DatasetKind.STAGING_TABLE: Layer.STAGING,
    DatasetKind.ODS_TABLE: Layer.ODS,
    DatasetKind.CORE_TABLE: Layer.CORE,
    DatasetKind.DIMENSION_TABLE: Layer.MART,
    DatasetKind.FACT_TABLE: Layer.MART,
    DatasetKind.AGGREGATE_TABLE: Layer.MART,
    DatasetKind.MART_TABLE: Layer.MART,
    DatasetKind.EXPORT_FILE: Layer.CONSUMPTION,
    DatasetKind.REPORT: Layer.CONSUMPTION,
    DatasetKind.DOWNSTREAM_SYSTEM: Layer.CONSUMPTION,
}

#: Dosya niteliği taşıyan veri nesneleri (file_spec bekleriz).
FILE_KINDS: frozenset[DatasetKind] = frozenset(
    {
        DatasetKind.INTERFACE_FILE,
        DatasetKind.LANDING_FILE,
        DatasetKind.EXPORT_FILE,
    }
)


class ProcessKind(str, Enum):
    EXTRACT_JOB = "extract_job"
    BATCH_JOB = "batch_job"
    FILE_TRANSFER = "file_transfer"
    DECODER = "decoder"
    ETL_MAPPING = "etl_mapping"
    LOADER_UTILITY = "loader_utility"
    STORED_PROCEDURE = "stored_procedure"
    SQL_SCRIPT = "sql_script"
    SHELL_SCRIPT = "shell_script"
    DQ_CHECK = "dq_check"
    RECONCILIATION = "reconciliation"
    ARCHIVE_PURGE = "archive_purge"
    MANUAL_STEP = "manual_step"


class EdgeKind(str, Enum):
    """Kenar tipleri.

    ÖNEMLİ YÖN KURALI: her kenar **akış yönünde** yazılır — ``source`` önce gelir,
    ``target`` sonra. Veri akışında bu "veri buradan buraya gider", yürütme
    sırasında ise "source önce koşar, target bekler" anlamına gelir.

    PLAN.md'de ``depends_on`` olarak geçen job bağımlılığı burada ``PRECEDES``
    adıyla ve yürütme sırası yönünde tutulur; böylece graftaki bütün kenarlar
    aynı yöne (ileri) bakar ve topolojik sıralama/kritik yol doğrudan çalışır.
    """

    READS = "reads"  # dataset -> process
    WRITES = "writes"  # process -> dataset
    PRODUCES_FILE = "produces_file"  # process -> dataset(file)
    CONSUMES_FILE = "consumes_file"  # dataset(file) -> process
    TRANSFERS = "transfers"  # dataset(file) -> dataset(file)
    PRECEDES = "precedes"  # process -> process (yürütme sırası)
    TRIGGERS = "triggers"  # dataset|process -> process (olay tetiklemesi)
    COLUMN_MAPS_TO = "column_maps_to"  # colref -> colref (direct lineage)
    INFLUENCES = "influences"  # colref -> colref (indirect: WHERE/JOIN/GROUP BY)
    DERIVES_FROM = "derives_from"  # dataset -> dataset (süreç modellenmemişse kısayol)
    VALIDATES = "validates"  # process -> dataset
    RECONCILES = "reconciles"  # process -> dataset
    IMPLEMENTS = "implements"  # any -> any (iş sözlüğü bağı)


#: Veri akışı kenarları — "veri A'dan B'ye gider" sorusunun cevabı bunlardan çıkar.
DATA_EDGES: frozenset[EdgeKind] = frozenset(
    {
        EdgeKind.READS,
        EdgeKind.WRITES,
        EdgeKind.PRODUCES_FILE,
        EdgeKind.CONSUMES_FILE,
        EdgeKind.TRANSFERS,
        EdgeKind.DERIVES_FROM,
    }
)

#: Yürütme sırası kenarları — batch zinciri, kritik yol, simülasyon bunları kullanır.
CONTROL_EDGES: frozenset[EdgeKind] = frozenset({EdgeKind.PRECEDES, EdgeKind.TRIGGERS})

#: Kolon seviyesi kenarlar.
COLUMN_EDGES: frozenset[EdgeKind] = frozenset({EdgeKind.COLUMN_MAPS_TO, EdgeKind.INFLUENCES})

#: Kontrol/denetim kenarları (ABC çatısı).
CHECK_EDGES: frozenset[EdgeKind] = frozenset({EdgeKind.VALIDATES, EdgeKind.RECONCILES})


class LoadStrategy(str, Enum):
    FULL = "full"
    DELTA = "delta"
    APPEND = "append"
    MERGE = "merge"
    CDC = "cdc"
    SCD1 = "scd1"
    SCD2 = "scd2"
    TRUNCATE_LOAD = "truncate_load"
    PARTITION_EXCHANGE = "partition_exchange"


class DeltaMethod(str, Enum):
    WATERMARK = "watermark"
    CDC_LOG = "cdc_log"
    FILE_STAMP = "file_stamp"
    HASH_COMPARE = "hash_compare"
    FULL_COMPARE = "full_compare"
    NONE = "none"


class Sensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    PII = "pii"


class Status(str, Enum):
    """Bir node/edge'in modele kabul durumu.

    LLM ve düşük güvenli çıkarımlar DAİMA ``DRAFT`` girer (PLAN.md 5.9.1).
    """

    DRAFT = "draft"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class SystemKind(str, Enum):
    SOURCE = "source"
    DWH = "dwh"
    BI = "bi"
    DOWNSTREAM = "downstream"
    TRANSFER = "transfer"
    SCHEDULER = "scheduler"


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

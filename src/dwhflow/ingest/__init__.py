"""Doküman → model. Dosya türüne göre okuyucu seçer, sonuçları tek modelde birleştirir.

    result = ingest_files([Path("sttm.xlsx"), Path("tasarim.docx")], llm=client, config=cfg)
    result.model      # kanonik model
    result.reports    # her doküman için ne çıkarıldı / neden çıkarılamadı
    result.leftovers  # tablo adı geçen ama kuralla çözülemeyen paragraflar (onay kuyruğu)

Okuyucu seçimi ve yeni biçim ekleme: bkz. ``registry``. Yerleşik biçimler burada,
ek biçimler ``formats/`` paketindeki modüllerde kaydedilir.
"""

from __future__ import annotations

import importlib
import pkgutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..model import Model
from . import preview as _preview
from .base import DocReport, ModelBuilder
from .config import DEFAULT, IngestConfig, active, using
from .readers import (
    read_csv,
    read_docx,
    read_excel,
    read_jil,
    read_pdf,
    read_sql,
    read_textfile,
    read_yaml,
)
from .registry import (
    REGISTRY,
    Block,
    FormatSpec,
    accepted_suffixes,
    catalog,
    mark_unsupported,
    register,
    resolve,
)

#: Bir yüklemede açılabilecek en çok doküman (arşivler dahil).
MAX_DOCUMENTS = 2000


def _looks_like_jil(head: bytes, name: str) -> bool:
    text = head.decode("utf-8", errors="ignore")
    return "insert_job" in text and any(line.strip().lower().startswith("insert_job")
                                        for line in text.splitlines()[:400])


def _register_builtins() -> None:
    register(FormatSpec("excel", "Excel", (".xlsx", ".xlsm"), read_excel, _preview.preview_excel,
                        group="Tablo", description="STTM/eşleme ve job tabloları; uzun metin hücreleri",
                        sample="ornek_dokumanlar/STTM_Kart_Islemleri.xlsx"))
    register(FormatSpec("csv", "CSV", (".csv", ".tsv"), read_csv, _preview.preview_csv,
                        group="Tablo", description="Ayırıcı otomatik bulunur (, ; sekme |)"))
    register(FormatSpec("word", "Word", (".docx",), read_docx, _preview.preview_docx,
                        description="Paragraflar (başlıklar konum olur) ve tablolar",
                        sample="ornek_dokumanlar/Kart_Tasarim_Dokumani.docx"))
    register(FormatSpec("pdf", "PDF", (".pdf",), read_pdf, _preview.preview_pdf,
                        description="Metin katmanı olan PDF'ler (taranmış görüntü okunmaz)",
                        sample="ornek_dokumanlar/BDDK_Bildirim_Sureci.pdf"))
    register(FormatSpec("metin", "Metin", (".md", ".txt"), read_textfile, _preview.preview_markdown,
                        description="Paragraflar ve Markdown tabloları",
                        sample="ornek_dokumanlar/operasyon_notlari.txt"))
    register(FormatSpec("sql", "SQL", (".sql", ".bteq"), read_sql, _preview.preview_sql,
                        group="Kod", description="INSERT…SELECT, CTAS, MERGE, UPDATE; her ifade ayrı süreç",
                        sample="ornek_dokumanlar/kart_mart_yukleme.sql"))
    register(FormatSpec("jil", "AutoSys JIL", (".jil",), read_jil, _preview.preview_code,
                        sniff=_looks_like_jil, group="Zamanlayıcı",
                        description="insert_job, condition: s()/d(), start_times",
                        sample="ornek_dokumanlar/kart_gece_zinciri.jil"))
    register(FormatSpec("yaml", "DWHFlow modeli", (".yaml", ".yml"), read_yaml, _preview.preview_code,
                        group="Model", description="DWHFlow YAML model dosyaları"))
    for suffix, message in {
        ".doc": "Eski Word biçimi (.doc) okunamıyor; Word'de .docx olarak kaydedip yükleyin.",
        ".xls": "Eski Excel biçimi (.xls) okunamıyor; .xlsx olarak kaydedip yükleyin.",
        ".vsdx": "Visio çizimleri henüz okunamıyor.",
        ".dtsx": "SSIS paketleri okunamıyor; SQL'lerini .sql olarak verebilirsiniz.",
        ".ktr": "Pentaho dönüşümleri okunamıyor.",
        ".kjb": "Pentaho job'ları okunamıyor.",
        ".dsx": "DataStage export'ları henüz okunamıyor.",
        ".xml": "Bu XML biçimi tanınmadı (içerik tespiti eşleşmedi).",
        ".cpy": "COBOL copybook'ları okunamıyor.",
        ".cbl": "COBOL kaynakları okunamıyor.",
        ".pptx": "PowerPoint henüz okunamıyor.",
        ".py": "Python dosyası; Airflow DAG'ı olarak tanınmadı.",
        ".json": "Bu JSON biçimi tanınmadı.",
    }.items():
        mark_unsupported(suffix, message)


def _load_format_plugins() -> list[str]:
    """``ingest/formats/`` altındaki modülleri yükler; bozuk bir eklenti diğerlerini engellemez."""
    from . import formats

    errors = []
    for module in sorted(pkgutil.iter_modules(formats.__path__), key=lambda m: m.name):
        if module.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"{formats.__name__}.{module.name}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{module.name}: {type(exc).__name__}: {exc}")
    return errors


_register_builtins()
PLUGIN_ERRORS = _load_format_plugins()

#: Geriye uyumluluk: uzantı → (etiket, okuyucu)
READERS: dict[str, tuple[str, Callable[..., Any]]] = {
    suffix: (REGISTRY.formats[key].label, REGISTRY.formats[key].reader)
    for suffix, key in REGISTRY.by_suffix.items()
}
UNSUPPORTED: dict[str, str] = REGISTRY.unsupported
ACCEPTED = accepted_suffixes()


@dataclass
class IngestResult:
    model: Model
    reports: list[DocReport]
    llm: dict[str, Any] | None = None
    llm_candidates: int = 0
    errors: list[str] = field(default_factory=list)
    #: (dosya, konum, paragraf) — kuralla çözülemeyenler (LLM'e gönderilen ya da onay bekleyen)
    leftovers: list[tuple[str, str, str]] = field(default_factory=list)
    #: (dosya, konum, başlıklar, ilk 3 satır) — tanınmayan tablolar (LLM başlık eşlemesi adayı)
    unrecognized_tables: list[tuple[str, str, list[str], list[list[str]]]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "documents": [r.as_dict() for r in self.reports],
            "llm": self.llm,
            "llm_candidates": self.llm_candidates,
            "stats": self.model.stats(),
        }


def _expand(paths: list[Path], workdir: Path, reports: list[DocReport]) -> list[tuple[Path, str | None]]:
    """Arşivleri (kayıtlı ``expander``'larla) açar. (dosya, içinden çıktığı arşiv) listesi."""
    out: list[tuple[Path, str | None]] = []
    for index, path in enumerate(paths):
        expander = REGISTRY.expanders.get(path.suffix.lower())
        if expander is None:
            out.append((path, None))
            continue
        target = workdir / f"{index:04d}-{path.stem}"
        target.mkdir(parents=True, exist_ok=True)
        try:
            inner = expander(path, target)
        except Exception as exc:  # noqa: BLE001 - bozuk arşiv yalnız kendini düşürsün
            report = DocReport(file=path.name, reader="Arşiv", status="error", format="arsiv")
            report.note(f"arşiv açılamadı: {type(exc).__name__}: {str(exc)[:200]}")
            reports.append(report)
            continue
        report = DocReport(file=path.name, reader="Arşiv", format="arsiv")
        report.note(f"{len(inner)} dosya çıkarıldı; her biri ayrı raporlanır")
        reports.append(report)
        out.extend((item, path.name) for item in inner)
    if len(out) > MAX_DOCUMENTS:
        raise ValueError(f"bir seferde en çok {MAX_DOCUMENTS} doküman okunabilir (arşivler dahil)")
    return out


def _read_one(path: Path, container: str | None, builder: ModelBuilder,
              config: IngestConfig) -> tuple[DocReport, list[tuple[str, str]]]:
    spec, how, message = resolve(path, config)
    if spec is None:
        report = DocReport(file=path.name, reader="—", status="unsupported", detected=how,
                           container=container)
        report.note(message)
        return report, []
    report = DocReport(file=path.name, reader=spec.label, format=spec.key, detected=how,
                       container=container)
    if message:
        report.note(message)
    if not spec.is_available():
        report.status = "unsupported"
        report.note(f"{spec.label} okuyucusu için ek paket gerekli: "
                    f'pip install "dwhflow[{spec.extra}]"' if spec.extra else
                    f"{spec.label} okuyucusunun bağımlılığı kurulu değil")
        return report, []
    try:
        leftovers = spec.reader(builder, report, path)
    except Exception as exc:  # noqa: BLE001 - tek dosya tüm dönüşümü düşürmesin
        report.status = "error"
        report.note(f"okunamadı: {type(exc).__name__}: {str(exc)[:200]}")
        leftovers = []
    if container:
        report.note(f"{container} arşivinden")
    return report, list(leftovers or [])


def ingest_files(paths: list[Path], *, llm: Any = None, name: str = "dokuman-modeli",
                 config: IngestConfig | None = None,
                 progress: Callable[[str], None] | None = None) -> IngestResult:
    """Dokümanları okuyup tek bir model üretir.

    ``config`` verilmezse etkin (varsayılan) okuma yapılandırması kullanılır.
    ``llm`` bir ``LLMClient`` ise, kuralların çözemediği paragraflar ona gönderilir.
    """
    config = config or active()
    say = progress or (lambda message: None)
    builder = ModelBuilder(name)
    reports: list[DocReport] = []
    candidates: list[tuple[str, str, str]] = []
    llm_info = None
    with using(config), tempfile.TemporaryDirectory(prefix="dwhflow-okuma-") as work:
        documents = _expand([Path(p) for p in paths], Path(work), reports)
        for number, (path, container) in enumerate(documents, start=1):
            say(f"{number}/{len(documents)}: {path.name}")
            report, leftovers = _read_one(path, container, builder, config)
            report.leftovers = len(leftovers)
            candidates.extend((path.name, where, text) for where, text in leftovers)
            if leftovers and llm is None:
                report.note(f"{len(leftovers)} paragrafta tablo adı geçiyor ama akış yönü kuralla "
                            "çıkarılamadı; onay kuyruğunda ya da LLM ile işlenebilir")
            reports.append(report)

        if llm is not None and candidates:
            from ..llm import extract_with_llm

            say(f"LLM: {len(candidates)} paragraf")
            stats = extract_with_llm(candidates, builder, {r.file: r for r in reports}, llm)
            llm_info = stats.as_dict()

        model = builder.build()
    _count(model, reports)
    return IngestResult(model=model, reports=reports, llm=llm_info, llm_candidates=len(candidates),
                        leftovers=candidates, errors=list(PLUGIN_ERRORS),
                        unrecognized_tables=list(builder.unrecognized_tables))


def preview_document(path: Path, config: IngestConfig | None = None) -> tuple[str | None, list[Block]]:
    """(biçim anahtarı, bloklar). Önizleyici yoksa metin olarak gösterilir."""
    config = config or DEFAULT
    with using(config):
        spec, _, _ = resolve(path, config)
        previewer = spec.preview if spec and spec.preview else _preview.preview_any
        try:
            return (spec.key if spec else None), previewer(path)
        except Exception as exc:  # noqa: BLE001 - önizleme hatası ekranda not olarak görünür
            return (spec.key if spec else None), [
                {"locator": "", "kind": "note", "text": f"Önizleme üretilemedi: {exc}"}]


def _count(model: Model, reports: list[DocReport]) -> None:
    """Rapor sayılarını modeldeki kaynak bilgisinden doldurur (birleştirme sonrası gerçek sayı)."""
    by_file = {r.file: r for r in reports}
    datasets: dict[str, set[str]] = {}
    processes: dict[str, set[str]] = {}
    for dataset in model.datasets.values():
        for source in dataset.sources:
            datasets.setdefault(source.file or "", set()).add(dataset.id)
    for process in model.processes.values():
        for source in process.sources:
            processes.setdefault(source.file or "", set()).add(process.id)
    for file, report in by_file.items():
        if report.format in ("yaml", "arsiv"):
            continue
        report.datasets = len(datasets.get(file, ()))
        report.processes = max(report.processes, len(processes.get(file, ())))
        if report.status == "ok" and not (report.datasets or report.processes or report.flows):
            report.status = "empty"
            report.note("bu dokümandan akış bilgisi çıkarılamadı")


__all__ = [
    "ACCEPTED", "READERS", "UNSUPPORTED", "IngestConfig", "IngestResult", "catalog",
    "ingest_files", "preview_document",
]

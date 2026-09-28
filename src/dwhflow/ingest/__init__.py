"""Doküman → model. Dosya türüne göre okuyucu seçer, sonuçları tek modelde birleştirir.

    result = ingest_files([Path("sttm.xlsx"), Path("tasarim.docx")], llm=client)
    result.model     # kanonik model
    result.reports   # her doküman için ne çıkarıldı / neden çıkarılamadı
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..model import Model
from .base import DocReport, ModelBuilder
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

Reader = Callable[[ModelBuilder, DocReport, Path], list[tuple[str, str]]]

READERS: dict[str, tuple[str, Reader]] = {
    ".xlsx": ("Excel", read_excel),
    ".xlsm": ("Excel", read_excel),
    ".csv": ("CSV", read_csv),
    ".tsv": ("CSV", read_csv),
    ".docx": ("Word", read_docx),
    ".pdf": ("PDF", read_pdf),
    ".md": ("Metin", read_textfile),
    ".txt": ("Metin", read_textfile),
    ".sql": ("SQL", read_sql),
    ".bteq": ("SQL", read_sql),
    ".jil": ("AutoSys JIL", read_jil),
    ".yaml": ("DWHFlow modeli", read_yaml),
    ".yml": ("DWHFlow modeli", read_yaml),
}

#: Tanınan ama bu sürümde okunamayan türler — ne yapılacağı açıkça söylenir.
UNSUPPORTED: dict[str, str] = {
    ".doc": "Eski Word biçimi (.doc) okunamıyor; Word'de .docx olarak kaydedip yükleyin.",
    ".xls": "Eski Excel biçimi (.xls) okunamıyor; .xlsx olarak kaydedip yükleyin.",
    ".vsdx": "Visio çizimleri henüz okunamıyor (planlı).",
    ".dtsx": "SSIS paketleri henüz okunamıyor (planlı); SQL'lerini .sql olarak verebilirsiniz.",
    ".ktr": "Pentaho dönüşümleri henüz okunamıyor (planlı).",
    ".kjb": "Pentaho job'ları henüz okunamıyor (planlı).",
    ".dsx": "DataStage export'ları henüz okunamıyor (planlı).",
    ".xml": "XML ETL export'ları henüz okunamıyor (planlı).",
    ".cpy": "COBOL copybook'ları henüz okunamıyor (planlı).",
    ".cbl": "COBOL kaynakları henüz okunamıyor (planlı).",
    ".pptx": "PowerPoint henüz okunamıyor.",
}

ACCEPTED = sorted(set(READERS) | set(UNSUPPORTED))


@dataclass
class IngestResult:
    model: Model
    reports: list[DocReport]
    llm: dict[str, Any] | None = None
    llm_candidates: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "documents": [r.as_dict() for r in self.reports],
            "llm": self.llm,
            "llm_candidates": self.llm_candidates,
            "stats": self.model.stats(),
        }


def ingest_files(paths: list[Path], *, llm: Any = None, name: str = "dokuman-modeli") -> IngestResult:
    """Dokümanları okuyup tek bir model üretir.

    ``llm`` bir ``LLMClient`` ise, kuralların çözemediği paragraflar ona gönderilir.
    """
    builder = ModelBuilder(name)
    reports: list[DocReport] = []
    candidates: list[tuple[str, str, str]] = []
    for path in paths:
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix in READERS:
            label, reader = READERS[suffix]
            report = DocReport(file=path.name, reader=label)
            try:
                leftovers = reader(builder, report, path)
            except Exception as exc:  # noqa: BLE001 - tek dosya tüm dönüşümü düşürmesin
                report.status = "error"
                report.note(f"okunamadı: {type(exc).__name__}: {str(exc)[:200]}")
                leftovers = []
            candidates.extend((path.name, where, text) for where, text in leftovers)
            if leftovers and llm is None:
                report.note(f"{len(leftovers)} paragrafta tablo adı geçiyor ama akış yönü kuralla "
                            "çıkarılamadı; LLM açılırsa bunlar da işlenebilir")
        else:
            report = DocReport(file=path.name, reader="—", status="unsupported")
            report.note(UNSUPPORTED.get(suffix, f"'{suffix or '(uzantısız)'}' türü desteklenmiyor"))
        reports.append(report)

    llm_info = None
    if llm is not None and candidates:
        from ..llm import extract_with_llm

        stats = extract_with_llm(candidates, builder, {r.file: r for r in reports}, llm)
        llm_info = stats.as_dict()

    model = builder.build()
    _count(model, reports)
    return IngestResult(model=model, reports=reports, llm=llm_info, llm_candidates=len(candidates))


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
        if report.reader == "DWHFlow modeli":
            continue
        report.datasets = len(datasets.get(file, ()))
        report.processes = max(report.processes, len(processes.get(file, ())))
        if report.status == "ok" and not (report.datasets or report.processes or report.flows):
            report.status = "empty"
            report.note("bu dokümandan akış bilgisi çıkarılamadı")


__all__ = ["ACCEPTED", "READERS", "UNSUPPORTED", "IngestResult", "ingest_files"]

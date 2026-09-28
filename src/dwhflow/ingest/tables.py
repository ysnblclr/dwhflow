"""Tablo yorumlayıcı: başlık + satırlar → akış olguları.

Excel sayfası, CSV, Word tablosu ve Markdown tablosu aynı yoldan geçer. Başlıklar
Türkçe/İngilizce eş anlamlılarla eşleştirilir; tablonun türü başlıklardan anlaşılır:

* **Eşleme (STTM)** — kaynak tablo + hedef tablo (+ kolonlar, dönüşüm, job).
* **Job listesi** — job + önceki job / bağımlılık (+ kaynak/hedef tablo).
* Tanınmayan tablo atlanır ve başlıklarıyla rapora yazılır; sessizce yutulmaz.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..model import ProcessKind
from .base import DocReport, ModelBuilder, fold, parse_load_strategy, ref

#: Rol → başlık eş anlamlıları (``fold`` edilmiş biçimde).
ROLES: dict[str, tuple[str, ...]] = {
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


@dataclass
class TableView:
    headers: list[str]
    rows: list[list[str]]
    locator: str  # ör. "STTM!A1" ya da "tablo 2"
    row_locator: str = "satır {n}"


def _match_roles(headers: list[str]) -> dict[str, int]:
    """Her role en iyi eşleşen kolon indeksini bulur (tam eşleşme > önek)."""
    folded = [fold(h) for h in headers]
    roles: dict[str, int] = {}
    used: set[int] = set()
    # Önce uzun/özel eş anlamlılar, sonra kısa olanlar: "kaynak kolon" "kaynak"tan önce eşleşsin.
    ordered = sorted(
        ((role, synonym) for role, synonyms in ROLES.items() for synonym in synonyms),
        key=lambda item: -len(item[1]),
    )
    for role, synonym in ordered:
        if role in roles:
            continue
        for index, header in enumerate(folded):
            if index in used or not header:
                continue
            if header == synonym or header.startswith(synonym + " ") or header.endswith(" " + synonym):
                roles[role] = index
                used.add(index)
                break
    return roles


def find_header(rows: list[list[str]], scan: int = 8) -> int | None:
    """Başlık satırını bulur: ilk ``scan`` satırda en çok rol eşleşen satır."""
    best, best_score = None, 1
    for index, row in enumerate(rows[:scan]):
        score = len(_match_roles([str(c or "") for c in row]))
        if score > best_score:
            best, best_score = index, score
    return best


def _cell(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return re.sub(r"\s+", " ", str(row[index] or "")).strip()


_SPLIT = re.compile(r"\s*(?:,|;|\n|\s\+\s|\bve\b|\band\b)\s*", re.IGNORECASE)


def _names(value: str) -> list[str]:
    """Bir hücrede birden fazla tablo olabilir: "STG_A, STG_B" ya da "STG_A ve STG_B"."""
    parts = [p.strip() for p in _SPLIT.split(value) if p and p.strip()]
    return [p for p in parts if re.search(r"[A-Za-z]", p) and len(p) <= 128]


def interpret(table: TableView, builder: ModelBuilder, report: DocReport, *,
              file: str, reader: str) -> int:
    """Tabloyu yorumlar, bulunan akış sayısını döner."""
    roles = _match_roles(table.headers)
    has_mapping = "target_table" in roles and "source_table" in roles
    has_jobs = "job" in roles and ("predecessor" in roles or "target_table" in roles
                                    or "source_table" in roles)
    if not (has_mapping or has_jobs):
        shown = ", ".join(h for h in table.headers if h)[:120]
        report.note(f"{table.locator}: tablo tanınmadı (başlıklar: {shown or '—'})")
        return 0

    flows = 0
    for number, row in enumerate(table.rows, start=1):
        if not any(str(c or "").strip() for c in row):
            continue
        locator = f"{table.locator} · {table.row_locator.format(n=number)}"
        source = ref(file, locator, reader, confidence=0.95)
        job_name = _cell(row, roles.get("job"))
        job_key = None
        if job_name:
            job_key = builder.process(
                job_name, source,
                tool=_cell(row, roles.get("tool")) or None,
                load_strategy=parse_load_strategy(_cell(row, roles.get("load"))),
                kind=ProcessKind.ETL_MAPPING,
            )
            for previous in _names(_cell(row, roles.get("predecessor"))):
                builder.precedes(builder.process(previous, source), job_key, source)

        sources = _names(_cell(row, roles.get("source_table")))
        targets = _names(_cell(row, roles.get("target_table")))
        source_system = _cell(row, roles.get("source_system")) or None
        target_system = _cell(row, roles.get("target_system")) or None
        transform = _cell(row, roles.get("transform")) or None
        source_keys = [builder.dataset(n, source, system=source_system) for n in sources]
        target_keys = [builder.dataset(n, source, system=target_system) for n in targets]

        for s_key in source_keys:
            for t_key in target_keys:
                if builder.flow(s_key, t_key, via=job_key, source=source, expression=transform):
                    flows += 1
        if job_key and not (source_keys and target_keys):
            for s_key in source_keys:
                builder.reads(s_key, job_key, source)
            for t_key in target_keys:
                builder.writes(job_key, t_key, source)

        source_col = _cell(row, roles.get("source_column"))
        target_col = _cell(row, roles.get("target_column"))
        if source_col and target_col and len(source_keys) == 1 and len(target_keys) == 1:
            builder.column_flow(source_keys[0], source_col, target_keys[0], target_col,
                                expression=transform, source=source)
    return flows


def rows_to_view(rows: list[list[Any]], locator: str, row_locator: str = "satır {n}") -> TableView | None:
    """Ham satırlardan başlığı bulup ``TableView`` üretir."""
    cleaned = [[("" if c is None else str(c)) for c in row] for row in rows]
    cleaned = [row for row in cleaned if any(c.strip() for c in row)]
    if len(cleaned) < 2:
        return None
    header_index = find_header(cleaned)
    if header_index is None:
        header_index = 0
    headers = cleaned[header_index]
    return TableView(headers=headers, rows=cleaned[header_index + 1:], locator=locator,
                     row_locator=row_locator)

"""IBM DataStage exportları → job süreçleri, okunan/yazılan tablo ve dosyalar, sequence sırası.

Biçimler:

* ``.dsx`` — klasik metin export (``BEGIN HEADER`` / ``BEGIN DSJOB`` / ``BEGIN DSRECORD`` …).
* DSExport XML — ``<DSExport><Job Identifier=…><Record Type=…><Property Name=…>``; uzantısı
  ``.xml`` olduğu için yalnız içerik tespitiyle seçilir.
* ``.isx`` — ``istool`` arşivi (zip); içindeki .dsx/DSExport biçimli iş tanımları güvenle açılır.

Model:

* Her paralel/server job bir süreçtir (``etl_mapping``, araç ``DataStage``).
* Veri stage'leri (connector'lar, sıralı dosya, dataset, eski veritabanı stage'leri): giriş
  bağlantısı olan stage **yazılan**, yalnız çıkış bağlantısı olan stage **okunan** nesnedir
  (lookup referansları da okunur; hedefin red/reject çıkışı okuma sayılmaz). Ad sırasıyla SQL'den
  (``readers._lineage``), ``TableName``/``table`` özelliklerinden ya da dosya yolunun son
  parçasından çıkar. Connector özellikleri (``XMLProperties``) gömülü XML'dir; defusedxml ile okunur.
* Job parametreleri: şema/veritabanı yerindeki ``#pDb#.`` ortama bağlıdır ve atılır; adın
  tamamı parametreyse sabit varsayılan değeri kullanılır, yoksa ad çözülemedi notu düşülür.
* Sequence job'ları ``batch_job`` sürecidir; Job Activity'lerin çağırdığı job'lar tetikleme
  bağlantılarının yönünde ``precedes`` kenarı alır. Sequencer, komut vb. ara adımlardan
  geçilir; hata dalları (Failed/hata) sıra sayılmaz.
* Transformer/lookup/join gibi ara stage'ler veri nesnesi üretmez (kolon türetmeleri okunmaz).
"""

from __future__ import annotations

import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from ...model import ProcessKind
from ..base import DocReport, ModelBuilder, clean_name, ref
from ..registry import REGISTRY, SNIFF_BYTES, Block, FormatSpec, register, register_expander
from ._common import (
    DEEP_MESSAGE,
    MAX_BYTES,
    PLACEHOLDER,
    XmlError,
    analyze_sql,
    decode,
    element_spans,
    local,
    looks_like_sql,
    parse_xml,
    read_capped,
    shorten,
    sniff_text,
    strip_bom,
    xml_to_text,
)

EXTRACTOR = "etl:datastage"
TOOL = "DataStage"
#: Önizlemede gösterilecek en çok karakter (tüm job blokları toplamı).
PREVIEW_CHARS = 2 * 1024 * 1024

#: İç içe blok/alt kayıt sınırı (gerçek exportlarda 3-4 düzey olur; kötü niyetli girdide özyineleme sınırı).
MAX_NESTING = 40

_JOB_TYPES = {"0": "server job", "1": "mainframe job", "2": "sequence", "3": "paralel job"}

# --------------------------------------------------------------------------- #
# Ortak ara yapı (DSX ve XML aynı yapıya çevrilir)
# --------------------------------------------------------------------------- #


@dataclass
class _Rec:
    """Bir DSRECORD/Record ya da DSSUBRECORD/SubRecord."""

    type: str = ""
    ident: str = ""
    props: dict[str, str] = field(default_factory=dict)
    collections: dict[str, list[_Rec]] = field(default_factory=dict)
    children: list[_Rec] = field(default_factory=list)

    def get(self, *names: str) -> str | None:
        wanted = [name.lower() for name in names]
        lowered = {key.lower(): value for key, value in self.props.items()}
        for name in wanted:
            if name in lowered:
                return lowered[name]
        return None

    def collection(self, name: str) -> list[_Rec]:
        return next((items for key, items in self.collections.items() if key.lower() == name.lower()), [])


@dataclass
class _Job:
    ident: str
    records: list[_Rec]
    line: int | None = None
    end_line: int | None = None
    element: Any = None  # XML öğesi (önizleme için)


def _norm_type(value: str) -> str:
    """``CCustomStage`` (DSX) ve ``CustomStage`` (XML) aynı ada iner."""
    value = (value or "").strip()
    if len(value) > 1 and value[0] == "C" and value[1].isupper():
        return value[1:]
    return value


def _job_name(job: _Job, index: int) -> str:
    root = _root(job)
    name = (job.ident or (root.get("Name") if root else None) or "").strip()
    return name or f"adsiz_job_{index}"


def _root(job: _Job) -> _Rec | None:
    return next((r for r in job.records if r.type == "JobDefn" or r.ident == "ROOT"), None)


# --------------------------------------------------------------------------- #
# .dsx ayrıştırıcı
# --------------------------------------------------------------------------- #

_BEGIN = re.compile(r"^\s*BEGIN\s+(\w+)\s*$")
_END = re.compile(r"^\s*END\s+(\w+)\s*$")
_KEYVAL = re.compile(r"^\s*([A-Za-z_$][\w.$\-]*)(?:\s+(.*?))?\s*$")
_MULTI = "=+=+=+="


class _DsxParser:
    """Satır tabanlı, hataya dayanıklı .dsx ayrıştırıcı (eksik END'ler üst bloğu kapatır)."""

    def __init__(self, text: str) -> None:
        self.lines = text.splitlines()
        self.index = 0
        self.open: list[str] = []
        self.problems: list[str] = []
        self.sections = 0

    def parse(self) -> tuple[list[_Job], Counter[str]]:
        jobs: list[_Job] = []
        skipped: Counter[str] = Counter()
        while self.index < len(self.lines):
            begin = _BEGIN.match(self.lines[self.index])
            if not begin:
                self.index += 1
                continue
            section = begin.group(1).upper()
            start = self.index + 1
            self.sections += 1
            record = self._block(section)
            if section == "DSJOB":
                for child in record.children:
                    child.type = _norm_type(child.props.get("OLEType", ""))
                    child.ident = child.props.get("Identifier", "")
                jobs.append(_Job(ident=record.props.get("Identifier", ""), records=record.children,
                                 line=start, end_line=self.index))
            elif section != "HEADER":
                skipped[section] += 1
        return jobs, skipped

    def _block(self, word: str) -> _Rec:
        record = _Rec()
        self.index += 1
        self.open.append(word)
        last_key = ""
        try:
            while self.index < len(self.lines):
                line = self.lines[self.index]
                end = _END.match(line)
                if end:
                    closing = end.group(1).upper()
                    if closing == word:
                        self.index += 1
                        return record
                    if closing in self.open:  # iç bloğun END'i eksik: üst bloğa bırak
                        self.problems.append(f"satır {self.index + 1}: 'END {word}' eksik")
                        return record
                    self.index += 1  # başıboş END satırı
                    continue
                begin = _BEGIN.match(line)
                if begin and len(self.open) >= MAX_NESTING:
                    self.problems.append(f"satır {self.index + 1}: çok derin iç içe blok atlandı")
                    self.index += 1
                    continue
                if begin:
                    child_word = begin.group(1).upper()
                    child = self._block(child_word)
                    if child_word == "DSSUBRECORD":
                        record.collections.setdefault(last_key or "_", []).append(child)
                    else:
                        record.children.append(child)
                    continue
                keyval = _KEYVAL.match(line)
                if keyval:
                    key = keyval.group(1)
                    record.props.setdefault(key, self._value(keyval.group(2) or ""))
                    last_key = key
                self.index += 1
            self.problems.append(f"'BEGIN {word}' kapanmadan dosya bitti")
            return record
        finally:
            self.open.pop()

    def _value(self, rest: str) -> str:
        if rest.startswith(_MULTI):
            tail = rest[len(_MULTI):]
            if tail.strip() and tail.rstrip().endswith(_MULTI):
                return tail.rstrip()[: -len(_MULTI)]
            parts = [tail] if tail.strip() else []
            self.index += 1
            while self.index < len(self.lines):
                line = self.lines[self.index]
                if line.rstrip().endswith(_MULTI):
                    last = line.rstrip()[: -len(_MULTI)]
                    if last.strip():
                        parts.append(last)
                    return "\n".join(parts)
                parts.append(line)
                self.index += 1
            self.problems.append("çok satırlı değer (=+=+=+=) kapanmadan dosya bitti")
            return "\n".join(parts)
        if rest.startswith('"'):
            out: list[str] = []
            position = 1
            while position < len(rest):
                char = rest[position]
                if char == "\\" and position + 1 < len(rest) and rest[position + 1] in '"\\':
                    out.append(rest[position + 1])
                    position += 2
                    continue
                if char == '"':
                    break
                out.append(char)
                position += 1
            return "".join(out)
        return rest


# --------------------------------------------------------------------------- #
# DSExport XML
# --------------------------------------------------------------------------- #


def _xml_record(element: Any, depth: int = 0) -> _Rec:
    record = _Rec(type=_norm_type(element.get("Type") or ""), ident=element.get("Identifier") or "")
    for child in element:
        tag = local(child.tag)
        if tag == "Property":
            name = child.get("Name")
            if name:
                record.props.setdefault(name, "".join(child.itertext()))
        elif tag == "Collection" and depth < MAX_NESTING:
            items = [_xml_record(sub, depth + 1) for sub in child if local(sub.tag) == "SubRecord"]
            record.collections.setdefault(child.get("Name") or "_", []).extend(items)
    return record


def _xml_jobs(root: Any) -> tuple[list[_Job], Counter[str]]:
    jobs: list[_Job] = []
    skipped: Counter[str] = Counter()
    for child in root:
        tag = local(child.tag)
        if tag == "Job":
            records = [_xml_record(r) for r in child if local(r.tag) == "Record"]
            jobs.append(_Job(ident=child.get("Identifier") or "", records=records, element=child))
        elif tag and tag != "Header":
            skipped[tag] += 1
    return jobs, skipped


# --------------------------------------------------------------------------- #
# Dosyayı job listesine çevirme
# --------------------------------------------------------------------------- #


@dataclass
class _Parsed:
    jobs: list[_Job]
    skipped: Counter[str]
    problems: list[str]
    text: str
    xml: bool


def _is_xml(text: str) -> bool:
    return text.lstrip().startswith("<")


def _parse(data: bytes) -> _Parsed:
    """Baytları job listesine çevirir. Tanınmayan/bozuk dosyada ``XmlError`` ya da ``ValueError``."""
    text = strip_bom(decode(data))
    if _is_xml(text) or data.startswith((b"\xff\xfe", b"\xfe\xff")):
        root = parse_xml(data)
        if local(root.tag) != "DSExport":
            raise ValueError(f"DataStage XML export'u değil (kök öğe <{local(root.tag)}>, <DSExport> bekleniyordu)")
        jobs, skipped = _xml_jobs(root)
        return _Parsed(jobs, skipped, [], text, True)
    parser = _DsxParser(text)
    jobs, skipped = parser.parse()
    if not parser.sections:
        raise ValueError("DataStage .dsx biçimi tanınmadı (BEGIN HEADER / BEGIN DSJOB yok)")
    return _Parsed(jobs, skipped, parser.problems, text, False)


# --------------------------------------------------------------------------- #
# Stage sınıflandırma ve ad çıkarma
# --------------------------------------------------------------------------- #

_DATA_TYPES = re.compile(
    r"connector|seqfile|sequentialfile|fileset|dataset|hashedfile|hashfile|flatfile|cff|externalsource|"
    r"externaltarget|teradata|tdmload|oracle|oraoci|db2|odbc|oledb|drs|netezza|sqlserver|mssql|sybase|"
    r"informix|mysql|postgres|greenplum|jdbc|hive|hdfs|bigquery|snowflake|redshift|salesforce|kafka",
    re.I)
_FILE_TYPES = re.compile(r"seqfile|sequentialfile|fileset|dataset|hashedfile|hashfile|flatfile|cff|external|file",
                         re.I)
_TABLE_KEYS = frozenset({"tablename", "table", "tablenames", "table_name", "targettable", "sourcetable"})
_FILE_KEYS = frozenset({"file", "filename", "filepath", "file_name", "dataset", "fileset", "lookupfileset",
                        "filepattern", "sourcefile", "targetfile"})
_SQL_SUFFIXES = ("sql", "statement", "statements", "query")
_NOT_SQL_KEYS = frozenset({"generatesql"})
_TRUE = frozenset({"1", "true", "yes", "y"})

_PARAM = re.compile(r"#([^#\s]+)#")
_FULL_PARAM = re.compile(r"#[^#\s]+#")
_DS_SEPARATOR = re.compile(r"\\\([0-9A-Fa-f]{1,4}\)")
_PATHLIKE = re.compile(r"[/\\]|#[^#\s]+#|\.\w{1,8}$")
_NAME_OK = re.compile(r"[\w$#@.\-]+")


def _is_extra_sql(key: str) -> bool:
    return key.startswith(("before", "after")) and "sql" in key


def _is_main_sql(key: str) -> bool:
    return key.endswith(_SQL_SUFFIXES) and key not in _NOT_SQL_KEYS and not _is_extra_sql(key)


def _is_pin(record: _Rec) -> bool:
    return record.type.endswith(("Input", "Output"))


def _is_stage(record: _Rec) -> bool:
    if not record.ident or record.type in ("JobDefn", "ContainerView") or _is_pin(record):
        return False
    if "Annotation" in record.type:
        return False
    return (record.type.endswith("Stage") or record.type.startswith("JS")
            or record.get("StageType", "InputPins", "OutputPins") is not None)


def _stage_type(stage: _Rec) -> str:
    return (stage.get("StageType") or stage.type or "").strip()


def _stage_name(stage: _Rec) -> str:
    return (stage.get("Name") or stage.ident).strip()


def _params(root: _Rec | None) -> dict[str, str]:
    """Sabit varsayılan değeri olan job parametreleri (``$PROJDEF`` gibi ortam değerleri hariç)."""
    out: dict[str, str] = {}
    for sub in root.collection("Parameters") if root else []:
        name, default = (sub.get("Name") or "").strip(), (sub.get("Default") or "").strip()
        if name and default and not default.startswith("$") and "#" not in default:
            out[name] = default
    return out


def _resolve_table(raw: str, params: dict[str, str]) -> tuple[str | None, str | None]:
    """(tablo adı, sorun). Şema yerindeki parametre atılır; adın tamamı parametreyse varsayılanı."""
    value = raw.strip().strip(";").strip()
    if not value:
        return None, None
    if _FULL_PARAM.fullmatch(value):
        default = params.get(value[1:-1])
        if not default:
            return None, f"tablo adı parametreye bağlı ({value})"
        value = default
    *prefix, last = [part.strip() for part in value.split(".")]
    prefix = [part for part in prefix if part and not _FULL_PARAM.fullmatch(part)]
    if _FULL_PARAM.fullmatch(last):
        default = params.get(last[1:-1])
        if not default:
            return None, f"tablo adı parametreye bağlı ({raw.strip()})"
        last = default
    name = clean_name(".".join([*prefix, last]))
    if not name or not _NAME_OK.fullmatch(name):
        return None, f"tablo adı anlaşılamadı ({shorten(raw, 80)})"
    return name, None


def _resolve_files(raw: str, params: dict[str, str]) -> tuple[list[str], list[str]]:
    """Dosya yolu özelliğinden dosya adları (son parça). DataStage iç ayırıcıları (``\\(2)``) temizlenir."""
    names: list[str] = []
    problems: list[str] = []
    for token in _DS_SEPARATOR.sub(" ", raw).split():
        token = token.strip().strip("\"'")
        if not token or not _PATHLIKE.search(token):
            continue
        if _FULL_PARAM.fullmatch(token):
            default = params.get(token[1:-1])
            if not default:
                problems.append(f"dosya adı parametreye bağlı ({token})")
                continue
            token = default
        base = re.split(r"[/\\]", token)[-1].strip()
        if _FULL_PARAM.fullmatch(base):
            default = params.get(base[1:-1])
            if not default:
                problems.append(f"dosya adı parametreye bağlı ({token})")
                continue
            base = re.split(r"[/\\]", default)[-1].strip()
        if base and base not in (".", "..") and re.search(r"[^\W\d]", base) and base not in names:
            names.append(base)
    return names, problems


def _prepare_sql(sql: str, params: dict[str, str]) -> str:
    """Parametreleri SQL'den ayıklar: ``#pDb#.`` atılır, tam tanıtıcı parametre sabit varsayılanla,
    çözülemeyenler ``PLACEHOLDER`` ile değiştirilir (o tablolar sonra sayılıp atılır)."""
    sql = re.sub(r"#[^#\s]+#\s*\.\s*", "", sql)

    def replace(match: re.Match[str]) -> str:
        start, end = match.span()
        before = sql[start - 1] if start else " "
        after = sql[end] if end < len(sql) else " "
        whole = not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_")
        default = params.get(match.group(1))
        return default if (whole and default) else PLACEHOLDER

    return _PARAM.sub(replace, sql)


def _connector_pairs(text: str) -> list[tuple[str, str]]:
    """Connector ``XMLProperties`` gömülü XML'inin yaprak öğeleri: (etiket, metin)."""
    root = parse_xml(text.strip())
    return [(local(el.tag), el.text.strip()) for el in root.iter()
            if len(el) == 0 and el.text and el.text.strip()]


@dataclass
class _Found:
    reads: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def add(self, names: list[str], name: str | None) -> None:
        if name and name not in names:
            names.append(name)


def _stage_pairs(records: list[_Rec], found: _Found) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for record in records:
        pairs.extend(record.props.items())
        for sub in record.collection("Properties"):
            key, value = sub.get("Name"), sub.get("Value")
            if key and value is not None:
                pairs.append((key, value))
    extra: list[tuple[str, str]] = []
    for key, value in pairs:
        if key.lower() == "xmlproperties" and value.strip():
            try:
                extra.extend(_connector_pairs(value))
            except XmlError as exc:
                found.problems.append(f"connector özellikleri (XMLProperties) okunamadı: {exc}")
    return [(key.lower(), value) for key, value in pairs + extra]


def _has_names(pairs: list[tuple[str, str]]) -> bool:
    return any(key in _TABLE_KEYS or key in _FILE_KEYS or
               ((_is_main_sql(key) or _is_extra_sql(key)) and looks_like_sql(value))
               for key, value in pairs)


def _stage_datasets(pairs: list[tuple[str, str]], file_stage: bool, role: str,
                    params: dict[str, str], found: _Found) -> None:
    """Bir veri stage'inin okuduğu/yazdığı nesneleri ``found``'a ekler."""
    reads, writes = found.reads, found.writes
    if file_stage:
        before = len(reads) + len(writes)
        for key, value in pairs:
            if key in _FILE_KEYS:
                names, problems = _resolve_files(value, params)
                found.problems.extend(problems)
                for name in names:
                    found.add(reads if role == "source" else writes, name)
        if len(reads) + len(writes) == before and not found.problems:
            found.problems.append("dosya adı bulunamadı")
        return

    def run(sql: str, main: bool) -> None:
        result = analyze_sql(_prepare_sql(sql, params))
        if result.unparsed:
            found.problems.append(f"{result.unparsed} SQL ifadesi ayrıştırılamadı")
        if result.unresolved:
            found.problems.append(f"{result.unresolved} tablo adı parametreye bağlı, çözülemedi")
        for statement in result.statements:
            if statement.target:
                found.add(writes, statement.target)
                for name in statement.sources:
                    found.add(reads, name)
            elif main and role == "source":
                for name in statement.sources:
                    found.add(reads, name)

    generated = any(key == "generatesql" and value.strip().lower() in _TRUE for key, value in pairs)
    if not generated:
        for key, value in pairs:
            if _is_main_sql(key) and looks_like_sql(value):
                run(value, main=True)
    direction = reads if role == "source" else writes
    if not direction:
        for key, value in pairs:
            if key in _TABLE_KEYS:
                for raw in re.split(r"[,\n]", value):
                    name, problem = _resolve_table(raw, params)
                    if problem:
                        found.problems.append(problem)
                    found.add(direction, name)
    for key, value in pairs:
        if _is_extra_sql(key) and looks_like_sql(value):
            run(value, main=False)
    if not (reads or writes) and not found.problems:
        found.problems.append("tablo adı bulunamadı")


# --------------------------------------------------------------------------- #
# Job çözümleme
# --------------------------------------------------------------------------- #

_FAILURE = re.compile(r"fail|abort|hata|ba[sş]ar[iı]s[iı]z|notok|not ok|error|exception", re.I)
_NEGATED_FAILURE = re.compile(r"(?:<>|!=|\bnot\b)\s*\S*fail", re.I)


def _is_failure_trigger(pin: _Rec) -> bool:
    texts = [pin.get("Name") or ""]
    texts += [value for key, value in pin.props.items()
              if any(word in key.lower() for word in ("condition", "expression", "trigger"))]
    text = " ".join(texts)
    return bool(_FAILURE.search(text)) and not _NEGATED_FAILURE.search(text)


class _JobReader:
    def __init__(self, builder: ModelBuilder, report: DocReport, file: str) -> None:
        self.builder, self.report, self.file = builder, report, file

    def ref(self, locator: str):
        return ref(self.file, locator, EXTRACTOR, confidence=1.0)

    def read(self, job: _Job, name: str) -> None:
        root = _root(job) or _Rec()
        params = _params(root)
        stages = {r.ident: r for r in job.records if _is_stage(r)}
        pins = {r.ident: r for r in job.records if _is_pin(r) and r.ident}
        job_type = (root.get("JobType") or "").strip()
        sequence = job_type == "2" or any(s.type.startswith("JS") for s in stages.values())
        kind = ProcessKind.BATCH_JOB if sequence else ProcessKind.ETL_MAPPING
        label = "sequence" if sequence else _JOB_TYPES.get(job_type, "job")
        parts = [f"DataStage {label}"]
        category = (root.get("Category") or "").strip()
        if category:
            parts.append(f"klasör {category}")
        text = (root.get("Description") or root.get("FullDescription") or "").strip()
        if text:
            parts.append(shorten(text.splitlines()[0], 240))
        if sequence:
            called = self._called_jobs(stages, params)
            if called:
                parts.append("çalıştırdığı job'lar: " + ", ".join(dict.fromkeys(called.values())))
        process = self.builder.process(name, self.ref(f"job {name}"), kind=kind, tool=TOOL,
                                       description=" · ".join(parts))
        self.report.processes += 1
        if sequence:
            self._sequence(name, stages, pins, params)
        else:
            self._data_stages(name, process, stages, pins, params)

    # -- paralel/server job ---------------------------------------------------- #

    def _pin_ids(self, stage: _Rec, pins: dict[str, _Rec], direction: str) -> list[str]:
        listed = stage.get("InputPins" if direction == "in" else "OutputPins")
        if listed is not None:
            return [p.strip() for p in listed.split("|") if p.strip()]
        suffix, prefix = ("Input" if direction == "in" else "Output"), stage.ident + "P"
        return [ident for ident, pin in pins.items()
                if ident.startswith(prefix) and ident[len(prefix):].isdigit() and pin.type.endswith(suffix)]

    def _data_stages(self, job: str, process: str, stages: dict[str, _Rec], pins: dict[str, _Rec],
                     params: dict[str, str]) -> None:
        seen: set[tuple[str, str]] = set()
        for stage in stages.values():
            stage_type = _stage_type(stage)
            where = f"job {job} · stage {_stage_name(stage)}"
            if "container" in f"{stage_type} {stage.type}".lower():
                if "shared" in f"{stage_type} {stage.type}".lower():
                    self.report.note(f"{where}: paylaşılan container içeriği okunmadı")
                continue
            inputs = self._pin_ids(stage, pins, "in")
            outputs = self._pin_ids(stage, pins, "out")
            found = _Found()
            records = [stage, *(pins[p] for p in inputs + outputs if p in pins)]
            pairs = _stage_pairs(records, found)
            if not (_DATA_TYPES.search(stage_type) or _has_names(pairs)):
                continue
            if not (inputs or outputs):
                continue
            role = "target" if inputs else "source"
            _stage_datasets(pairs, bool(_FILE_TYPES.search(stage_type)), role, params, found)
            for problem in found.problems:
                self.report.note(f"{where}: {problem}")
            source = self.ref(where)
            for name in found.reads:
                key = self.builder.dataset(name, source)
                self.builder.reads(key, process, source)
                if (key, "r") not in seen:
                    seen.add((key, "r"))
                    self.report.flows += 1
            for name in found.writes:
                key = self.builder.dataset(name, source)
                self.builder.writes(process, key, source)
                if (key, "w") not in seen:
                    seen.add((key, "w"))
                    self.report.flows += 1

    # -- sequence ------------------------------------------------------------ #

    def _called_jobs(self, stages: dict[str, _Rec], params: dict[str, str]) -> dict[str, str]:
        """Job Activity kimliği → çağırdığı job adı."""
        called: dict[str, str] = {}
        for ident, stage in stages.items():
            raw = stage.get("Jobname", "JobName", "JobNameToRun")
            if raw is None or not stage.type.startswith("JS"):
                continue
            value = raw.strip()
            if _FULL_PARAM.fullmatch(value):
                value = params.get(value[1:-1], "")
            if value and "#" not in value:
                called[ident] = value.split(".")[0] if re.fullmatch(r"[\w]+\.\w+", value) else value
        return called

    def _sequence(self, job: str, stages: dict[str, _Rec], pins: dict[str, _Rec],
                  params: dict[str, str]) -> None:
        called = self._called_jobs(stages, params)
        for ident, stage in stages.items():
            if stage.get("Jobname", "JobName", "JobNameToRun") is not None and ident not in called:
                self.report.note(f"job {job} · stage {_stage_name(stage)}: çağrılan job adı çözülemedi "
                                 f"({stage.get('Jobname', 'JobName', 'JobNameToRun')})")
        keys = {}
        for ident, target in called.items():
            source = self.ref(f"job {job} · stage {_stage_name(stages[ident])}")
            keys[ident] = self.builder.process(target, source, tool=TOOL)
        upstream: dict[str, list[str]] = {}
        for ident, stage in stages.items():
            for pin_id in self._pin_ids(stage, pins, "out"):
                pin = pins.get(pin_id)
                if pin is None:
                    continue
                partner = (pin.get("Partner") or "").split("|")[0].strip()
                if partner not in stages:
                    continue
                if _is_failure_trigger(pin):
                    self.report.note(f"job {job} · stage {_stage_name(stage)}: hata dalı "
                                     f"({pin.get('Name') or pin_id}) sıra kenarı sayılmadı")
                    continue
                upstream.setdefault(partner, []).append(ident)
        added: set[tuple[str, str]] = set()
        for ident in called:
            for previous in self._job_predecessors(ident, upstream, called):
                pair = (keys[previous], keys[ident])
                self.builder.precedes(*pair, self.ref(f"job {job} · stage {_stage_name(stages[previous])}"))
                if pair[0] != pair[1] and pair not in added:
                    added.add(pair)
                    self.report.flows += 1

    @staticmethod
    def _job_predecessors(start: str, upstream: dict[str, list[str]], jobs: dict[str, str]) -> list[str]:
        """Tetikleme zincirinde geriye doğru en yakın Job Activity'ler (ara adımlardan geçilir)."""
        found: list[str] = []
        seen, stack = {start}, [start]
        while stack:
            node = stack.pop()
            for previous in upstream.get(node, ()):
                if previous in seen:
                    continue
                seen.add(previous)
                if previous in jobs:
                    found.append(previous)
                else:
                    stack.append(previous)
        return found


# --------------------------------------------------------------------------- #
# Okuyucu, önizleme, içerik tespiti
# --------------------------------------------------------------------------- #


def read_datastage(builder: ModelBuilder, report: DocReport, path: Path) -> list[tuple[str, str]]:
    data = read_capped(path, report)
    if data is None:
        return []
    try:
        parsed = _parse(data)
    except (XmlError, ValueError) as exc:
        report.status = "error"
        report.note(str(exc))
        return []
    except RecursionError:
        report.status = "error"
        report.note(DEEP_MESSAGE)
        return []
    for problem in parsed.problems[:5]:
        report.note(problem)
    if parsed.skipped:
        report.note("job dışı bölümler atlandı: " + ", ".join(
            f"{name} ×{count}" for name, count in sorted(parsed.skipped.items())))
    if not parsed.jobs:
        report.status = "empty"
        report.note("dosyada DataStage job'ı bulunamadı")
        return []
    reader = _JobReader(builder, report, path.name)
    for index, job in enumerate(parsed.jobs, start=1):
        name = _job_name(job, index)
        try:
            reader.read(job, name)
        except Exception as exc:  # noqa: BLE001 - bozuk bir job diğerlerini düşürmesin
            report.note(f"job {name}: beklenmeyen hata, atlandı ({type(exc).__name__}: {str(exc)[:120]})")
    return []


def preview_datastage(path: Path) -> list[Block]:
    """Her job bir kod bloğu (konum ``job <ad>``); okuyucunun ``job X · stage Y`` konumları bu bloğa düşer."""
    report = DocReport(file=path.name, reader="önizleme")
    data = read_capped(path, report)
    if data is None:
        return [{"locator": "", "kind": "note", "text": "; ".join(report.notes)}]
    try:
        parsed = _parse(data)
    except (XmlError, ValueError) as exc:
        return [{"locator": "", "kind": "note", "text": f"Önizleme üretilemedi: {exc}"}]
    except RecursionError:
        return [{"locator": "", "kind": "note", "text": f"Önizleme üretilemedi: {DEEP_MESSAGE}"}]
    blocks: list[Block] = []
    spans = element_spans(parsed.text, "Job") if parsed.xml else []
    lines = parsed.text.splitlines() if not parsed.xml else []
    budget = PREVIEW_CHARS
    for index, job in enumerate(parsed.jobs, start=1):
        name = _job_name(job, index)
        block: Block = {"locator": f"job {name}", "kind": "code"}
        if parsed.xml and len(spans) == len(parsed.jobs):
            start, end, line = spans[index - 1]
            block.update(text=parsed.text[start:end].replace("\r\n", "\n"), line_start=line)
        elif parsed.xml:
            block["text"] = xml_to_text(job.element)
        else:
            block.update(text="\n".join(lines[(job.line or 1) - 1: job.end_line or len(lines)]),
                         line_start=job.line or 1)
        if len(block["text"]) > budget:
            blocks.append({"locator": "", "kind": "note",
                           "text": f"Önizleme sınırı: {len(parsed.jobs) - index + 1} job daha gösterilmedi."})
            break
        budget -= len(block["text"])
        blocks.append(block)
    if not parsed.jobs:
        blocks.append({"locator": "", "kind": "note", "text": "Dosyada DataStage job'ı bulunamadı."})
    return blocks


def looks_like_dsx(head: bytes, name: str) -> bool:
    first = sniff_text(head).lstrip().split("\n", 1)[0].strip()
    return first in ("BEGIN HEADER", "BEGIN DSJOB")


def looks_like_dsexport(head: bytes, name: str) -> bool:
    text = sniff_text(head).lstrip()
    return text.startswith("<") and re.search(r"<DSExport[\s>]", text) is not None


# --------------------------------------------------------------------------- #
# .isx arşivi
# --------------------------------------------------------------------------- #

MAX_ISX_ENTRIES = 2000
MAX_ISX_BYTES = 256 * 1024 * 1024
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _safe_name(raw: str, wanted: str, used: set[str]) -> str:
    base = PurePosixPath(raw.replace("\\", "/")).name
    base = re.sub(r"[^\w.\-]", "_", base).lstrip(".") or "girdi"
    stem, dot, suffix = base.rpartition(".")
    if not dot:
        stem, suffix = base, ""
    if stem.upper() in _RESERVED:
        base = f"_{base}"
    current = f".{suffix.lower()}" if suffix else ""
    if current != wanted and current in REGISTRY.by_suffix:
        base += wanted  # başka bir biçimin uzantısı: okuyucu yanlış seçilmesin
    candidate, number = base, 2
    while candidate.lower() in used:
        stem, dot, suffix = base.rpartition(".")
        candidate = f"{stem}_{number}.{suffix}" if dot else f"{base}_{number}"
        number += 1
    used.add(candidate.lower())
    return candidate


def expand_isx(path: Path, workdir: Path) -> list[Path]:
    """``istool`` arşivinden DataStage exportlarını çıkarır.

    Güvenlik: arşivdeki yollar kullanılmaz (yalnız temizlenmiş dosya adı; zip-slip olmaz), öğe
    sayısı, öğe boyutu ve açılan toplam bayt sınırlıdır; şifreli öğeler atlanır.
    """
    out: list[Path] = []
    used: set[str] = set()
    total = 0
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ISX_ENTRIES:
            raise ValueError(f"ISX arşivinde çok fazla öğe var ({len(infos)}; en çok {MAX_ISX_ENTRIES})")
        for info in infos:
            if info.is_dir() or info.file_size > MAX_BYTES or info.flag_bits & 0x1:
                continue
            try:
                with archive.open(info) as handle:
                    head = handle.read(SNIFF_BYTES)
                    total += len(head)
                    if looks_like_dsx(head, info.filename):
                        wanted = ".dsx"
                    elif looks_like_dsexport(head, info.filename):
                        wanted = ".xml"
                    else:
                        continue
                    rest = handle.read(MAX_BYTES + 1 - len(head))
            except Exception:  # noqa: BLE001 - bozuk/şifreli/desteklenmeyen sıkıştırmalı öğe atlanır
                continue
            data = head + rest
            total += len(rest)
            if total > MAX_ISX_BYTES:
                raise ValueError("ISX arşivi açıldığında çok büyük")
            if len(data) > MAX_BYTES:
                continue
            target = workdir / _safe_name(info.filename, wanted, used)
            target.write_bytes(data)
            out.append(target)
    if not out:
        raise ValueError("ISX arşivinde DataStage export'u (.dsx ya da DSExport XML) bulunamadı")
    return out


register(FormatSpec(
    "datastage", "IBM DataStage", (".dsx",), read_datastage, preview_datastage, sniff=looks_like_dsx,
    group="ETL", sample="ornek_bicimler/kart_datastage.dsx",
    description="DataStage .dsx exportu: job → süreç, connector/dosya stage'leri → tablo/dosya, "
                "sequence → job sırası",
))
register(FormatSpec(
    "datastage_xml", "IBM DataStage XML", (".xml",), read_datastage, preview_datastage,
    sniff=looks_like_dsexport, group="ETL", sample="ornek_bicimler/kart_datastage_export.xml",
    description="DataStage XML exportu (<DSExport>); .dsx ile aynı model",
), primary=False)
register_expander((".isx",), expand_isx)

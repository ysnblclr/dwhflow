"""Control-M iş tanımları → job süreçleri, zamanlama ve koşullardan job sırası.

Biçimler (uzantıları genel olduğundan ikisi de yalnız içerik tespitiyle seçilir):

* XML dışa aktarımı: ``<DEFTABLE>`` altında ``FOLDER`` / ``SMART_FOLDER`` / ``SUB_FOLDER`` /
  ``TABLE`` ve içlerinde ``<JOB JOBNAME=… TIMEFROM=… WEEKDAYS=…>`` ile ``INCOND``/``OUTCOND``.
* Automation API JSON: ``{"Klasör": {"Type": "Folder", "JobA": {"Type": "Job:Command", …}}}``;
  olaylar ``WaitForEvents``/``AddEvents``/``DeleteEvents``, sıra ``{"Type": "Flow", "Sequence": […]}``.

Model:

* Her job bir süreçtir (``batch_job``, araç ``Control-M``); başlama saati (TIMEFROM/FromTime),
  hafta günleri ve takvim ``Schedule``'a, açıklama/komut/uygulama bilgisi açıklamaya yazılır.
* Bir job'ın beklediği koşulu (INCOND/WaitForEvents) **ekleyen** (OUTCOND ``+``/``ADD``,
  AddEvents) job ondan önce koşar → ``precedes``. VEYA (OR) koşullarında her üreticiden kenar
  eklenir ve not düşülür. Hiçbir job'ın üretmediği koşullar dış koşul olarak not edilir; önceki
  güne (PREV) bağlı koşullar ve silinen koşullar sıra sayılmaz; yalnız ON/DOCOND ile (belirli bir
  sonuçta) eklenen koşullar da sıra sayılmaz, not edilir.
* Komut satırındaki DataStage (``dsjob -run``), SQL/BTEQ ve TPT çağrıları yalnız açıklamaya
  yazılır (başka bir sürecin adıyla eşleştirilmez); parolalar maskelenir.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...model import ProcessKind, Schedule
from ..base import DocReport, ModelBuilder, ref
from ..registry import Block, FormatSpec, register
from ._common import (
    DEEP_MESSAGE,
    XmlError,
    decode,
    element_spans,
    local,
    mask_secrets,
    parse_xml,
    read_capped,
    shorten,
    sniff_text,
    strip_bom,
    xml_to_text,
)

EXTRACTOR = "scheduler:controlm"
TOOL = "Control-M"
MAX_DEPTH = 50

_CONTAINERS = frozenset({"FOLDER", "SMART_FOLDER", "SUB_FOLDER", "TABLE", "SMART_TABLE", "SCHED_TABLE",
                         "SCHED_GROUP", "SUB_TABLE"})
_FOLDER_TYPES = frozenset({"Folder", "SimpleFolder", "SubFolder"})
#: Aynı çalıştırmaya (sipariş gününe) ait koşul tarihleri; diğerleri (PREV, NEXT, MMDD) sıra sayılmaz.
_SAME_RUN = frozenset({"", "ODAT", "****", "$$$$", "STAT", "ORDERDATE", "ANYDATE", "NODATE"})
_WEEK = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_CTM_DIGITS = {"0": "sun", "1": "mon", "2": "tue", "3": "wed", "4": "thu", "5": "fri", "6": "sat"}
_DAY_NAMES = frozenset({"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"})


@dataclass
class _Cond:
    name: str
    date: str = ""
    op: str = "and"

    @property
    def same_run(self) -> bool:
        return self.date.strip().upper() in _SAME_RUN


@dataclass(eq=False)
class _CtmJob:
    name: str
    folder: str
    description: str | None = None
    schedule: Schedule | None = None
    waits: list[_Cond] = field(default_factory=list)
    adds: list[str] = field(default_factory=list)
    deletes: list[str] = field(default_factory=list)
    conditional_adds: list[str] = field(default_factory=list)
    raw: Any = None

    @property
    def locator(self) -> str:
        return f"klasör {self.folder} · job {self.name}" if self.folder else f"job {self.name}"


@dataclass
class _Flow:
    folder: str
    name: str
    sequence: list[str]
    raw: Any = None

    @property
    def locator(self) -> str:
        return f"klasör {self.folder} · akış {self.name}" if self.folder else f"akış {self.name}"


@dataclass
class _Parsed:
    #: Adı olan job'lar (okuyucu bunları kullanır).
    jobs: list[_CtmJob]
    flows: list[_Flow]
    #: Belge sırasıyla bütün JOB öğeleri, adsızlar dahil (XML önizlemesi ham konumlarla eşleştirir).
    all_jobs: list[_CtmJob]
    text: str
    xml: bool

    @property
    def unnamed(self) -> int:
        return len(self.all_jobs) - len(self.jobs)


# --------------------------------------------------------------------------- #
# Zamanlama ve açıklama
# --------------------------------------------------------------------------- #


def _time(value: object) -> str | None:
    match = re.fullmatch(r"(\d{1,2}):?(\d{2})", str(value or "").strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    return f"{hour:02d}:{minute:02d}" if hour < 24 and minute < 60 else None


def _weekdays(value: object) -> tuple[list[str], str | None]:
    """(gün listesi, çözülemediyse ham değer). Control-M sayıları: 0 = pazar … 6 = cumartesi."""
    tokens = value if isinstance(value, list) else re.split(r"[,\s]+", str(value or ""))
    tokens = [str(t).strip() for t in tokens if str(t).strip()]
    if not tokens:
        return [], None
    days: set[str] = set()
    for token in tokens:
        lowered = token.lower()
        if lowered == "all":
            return list(_WEEK), None
        if token in _CTM_DIGITS:
            days.add(_CTM_DIGITS[token])
        elif lowered in _WEEK or lowered in _DAY_NAMES:
            days.add(lowered[:3])
        else:
            return [], ",".join(tokens)
    return [d for d in _WEEK if d in days], None


def _schedule(start: object, weekdays: object, calendar: object) -> tuple[Schedule | None, str | None]:
    time = _time(start)
    days, raw_days = _weekdays(weekdays)
    calendar = str(calendar).strip() if isinstance(calendar, str) and calendar.strip() else None
    if not (time or days or calendar):
        return None, raw_days
    return Schedule(start_times=[time] if time else [], days_of_week=days, calendar=calendar), raw_days


_DSJOB = re.compile(r"(?:^|[\s/\\;&|(])dsjob(?:\.exe)?\s+(?P<args>.*)", re.I)
_DSJOB_WITH_ARG = frozenset({"-param", "-mode", "-warn", "-rows", "-queue", "-opmetadata", "-paramfile"})
_STOP = (">", "|", "&", ";", "2>", "1>")
_SCRIPT = re.compile(r"[\w./\\$%{}~-]*\.(sql|bteq|btq|tpt)\b", re.I)


def _datastage_call(command: str) -> tuple[str, str] | None:
    """``dsjob … -run [seçenekler] <proje> <job>`` → (proje, job)."""
    match = _DSJOB.search(command)
    if not match:
        return None
    tokens = match.group("args").split()
    lowered = [token.lower() for token in tokens]
    if "-run" not in lowered:
        return None
    positional: list[str] = []
    skip = False
    for token in tokens[lowered.index("-run") + 1:]:
        if skip:
            skip = False
            continue
        if token.startswith(_STOP):
            break
        if token.startswith("-"):
            skip = token.lower() in _DSJOB_WITH_ARG
            continue
        positional.append(token.strip("\"'"))
        if len(positional) == 2:
            break
    if len(positional) < 2:
        return None
    project, job = positional
    return project, job.split(".")[0]


def _command_hints(command: str) -> list[str]:
    hints: list[str] = []
    call = _datastage_call(command)
    if call:
        hints.append(f"DataStage job'ı {call[1]} (proje {call[0]})")
    bteq = re.search(r"\bbteq\b", command, re.I) is not None
    for match in _SCRIPT.finditer(command):
        script, extension = match.group(0), match.group(1).lower()
        if extension == "tpt":
            hints.append(f"TPT betiği {script}")
        elif bteq or extension in ("bteq", "btq"):
            hints.append(f"BTEQ betiği {script}")
        else:
            hints.append(f"SQL betiği {script}")
    return list(dict.fromkeys(hints))


def _describe(text: object, *, dummy: bool, command: str | None, script: str | None, extra_hints: list[str],
              application: str | None, host: str | None, month_days: str | None, raw_days: str | None,
              cyclic: bool) -> str | None:
    parts: list[str] = []
    if isinstance(text, str) and text.strip():
        parts.append(shorten(text, 240))
    if dummy:
        parts.append("Dummy (boş) job")
    hints = list(extra_hints)
    if command:
        hints = [*_command_hints(command), *hints]
    if script:
        hints = [*_command_hints(script), *hints]
    parts.extend(dict.fromkeys(hints))
    if command:
        parts.append(f"komut: {shorten(mask_secrets(command))}")
    elif script:
        parts.append(f"betik: {shorten(mask_secrets(script))}")
    if application:
        parts.append(f"uygulama: {application}")
    if host:
        parts.append(f"sunucu: {host}")
    if month_days:
        parts.append(f"ayın günleri: {month_days}")
    if raw_days:
        parts.append(f"hafta günleri: {raw_days}")
    if cyclic:
        parts.append("döngüsel (cyclic) job")
    return " · ".join(parts) or None


def _month_days(value: object) -> str | None:
    tokens = value if isinstance(value, list) else re.split(r"[,\s]+", str(value or ""))
    tokens = [str(t).strip() for t in tokens if str(t).strip()]
    if not tokens or any(t.upper() == "ALL" for t in tokens):
        return None
    return ",".join(tokens)


# --------------------------------------------------------------------------- #
# XML
# --------------------------------------------------------------------------- #


def _attrs(element: Any) -> dict[str, str]:
    return {str(key).upper(): str(value) for key, value in element.attrib.items()}


def _xml_job(element: Any, folder: str) -> _CtmJob:
    a = _attrs(element)
    job = _CtmJob(name=(a.get("JOBNAME") or "").strip(), folder=folder, raw=element)
    for child in element:
        tag = local(child.tag).upper()
        c = _attrs(child)
        name = (c.get("NAME") or "").strip()
        if tag == "INCOND" and name:
            op = "or" if c.get("AND_OR", "").strip().upper() in ("O", "OR") else "and"
            job.waits.append(_Cond(name, c.get("ODATE", ""), op))
        elif tag == "OUTCOND" and name:
            sign = (c.get("SIGN") or "+").strip().upper()
            if sign in ("+", "ADD"):
                job.adds.append(name)
            elif sign in ("-", "DEL", "DELETE"):
                job.deletes.append(name)
        elif tag == "ON":
            for action in child.iter():
                d = _attrs(action)
                if (local(action.tag).upper() == "DOCOND" and (d.get("NAME") or "").strip()
                        and (d.get("SIGN") or "+").strip().upper() in ("+", "ADD")):
                    job.conditional_adds.append(d["NAME"].strip())
    job.schedule, raw_days = _schedule(a.get("TIMEFROM"), a.get("WEEKDAYS"),
                                       a.get("DAYSCAL") or a.get("DAYS_CAL") or a.get("WEEKSCAL")
                                       or a.get("WEEKS_CAL") or a.get("CONFCAL") or a.get("CONF_CAL"))
    task_type = (a.get("TASKTYPE") or "").strip().lower()
    command = (a.get("CMDLINE") or "").strip() or None
    script = None
    if not command and (a.get("MEMNAME") or "").strip():
        script = "/".join(p.strip().rstrip("/") for p in (a.get("MEMLIB", ""), a["MEMNAME"]) if p.strip())
    application = " / ".join(v.strip() for v in (a.get("APPLICATION", ""), a.get("SUB_APPLICATION", ""))
                             if v.strip()) or None
    job.description = _describe(
        a.get("DESCRIPTION"), dummy=task_type == "dummy", command=command, script=script, extra_hints=[],
        application=application, host=(a.get("NODEID") or "").strip() or None,
        month_days=_month_days(a.get("DAYS")), raw_days=raw_days, cyclic=a.get("CYCLIC", "").strip() == "1")
    return job


def _folder_name(element: Any, tag: str) -> str:
    a = _attrs(element)
    for key in ("FOLDER_NAME", "TABLE_NAME", "JOBNAME", "GROUP"):
        if (a.get(key) or "").strip():
            return a[key].strip()
    return tag.lower()


def _xml_jobs(root: Any) -> list[_CtmJob]:
    jobs: list[_CtmJob] = []

    def walk(element: Any, path: list[str]) -> None:
        if len(path) > MAX_DEPTH:
            return
        for child in element:
            tag = local(child.tag).upper()
            if tag == "JOB":
                jobs.append(_xml_job(child, "/".join(path)))
            elif tag in _CONTAINERS:
                walk(child, [*path, _folder_name(child, tag)])

    root_tag = local(root.tag).upper()
    if root_tag == "JOB":
        jobs.append(_xml_job(root, ""))
    elif root_tag in _CONTAINERS:
        walk(root, [_folder_name(root, root_tag)])
    else:
        walk(root, [])
    return jobs


# --------------------------------------------------------------------------- #
# Automation API JSON
# --------------------------------------------------------------------------- #


def _events(items: object, target: list[_Cond]) -> None:
    op = "and"
    for item in items if isinstance(items, list) else []:
        if isinstance(item, str):
            token = item.strip().upper()
            if token in ("OR", "|"):
                op = "or"
                continue
            if token in ("AND", "&", "(", ")", ""):
                if token in ("AND", "&"):
                    op = "and"
                continue
            name, date = item.strip(), ""
        elif isinstance(item, dict):
            name, date = str(item.get("Event") or "").strip(), str(item.get("Date") or "")
        else:
            continue
        if name:
            target.append(_Cond(name, date, op))
            op = "and"


def _event_names(items: object) -> list[str]:
    found: list[_Cond] = []
    _events(items, found)
    return [cond.name for cond in found]


def _json_job(name: str, obj: dict[str, Any], folder: str, defaults: dict[str, Any]) -> _CtmJob:
    def get(key: str) -> Any:
        value = obj.get(key)
        if value in (None, ""):
            value = defaults.get(key)
        return value if isinstance(value, (str, int, float)) else None

    job = _CtmJob(name=name.strip(), folder=folder, raw=obj)
    for key, value in obj.items():
        if not isinstance(value, dict):
            continue
        kind, lowered = str(value.get("Type", "")), key.lower()
        if kind == "WaitForEvents" or lowered == "eventstowaitfor":
            _events(value.get("Events"), job.waits)
        elif kind == "AddEvents" or lowered == "eventstoadd":
            job.adds.extend(_event_names(value.get("Events")))
        elif kind == "DeleteEvents" or lowered == "eventstodelete":
            job.deletes.extend(_event_names(value.get("Events")))
        elif kind.startswith("If"):
            for action in value.values():
                if isinstance(action, dict) and str(action.get("Type", "")) in ("Event:Add", "Action:AddEvent"):
                    if action.get("Event"):
                        job.conditional_adds.append(str(action["Event"]).strip())
                    job.conditional_adds.extend(_event_names(action.get("Events")))
    when = obj.get("When") if isinstance(obj.get("When"), dict) else {}
    calendar = next((when.get(k) for k in ("WeekDaysCalendar", "MonthDaysCalendar", "Calendar")
                     if isinstance(when.get(k), str) and when.get(k).strip()), None)
    job.schedule, raw_days = _schedule(when.get("FromTime"), when.get("WeekDays"), calendar)
    kind = str(obj.get("Type", ""))
    hints: list[str] = []
    if isinstance(obj.get("SQLScript"), str):
        hints.append(f"SQL betiği {obj['SQLScript']}")
    if isinstance(obj.get("StoredProcedure"), str):
        schema = obj.get("Schema") if isinstance(obj.get("Schema"), str) else ""
        hints.append(f"saklı yordam {'.'.join(p for p in (schema, obj['StoredProcedure']) if p)}")
    if isinstance(obj.get("Query"), str):
        hints.append("gömülü SQL sorgusu")
    if isinstance(obj.get("Script"), str):
        hints.append("gömülü betik")
    script = None
    if isinstance(obj.get("FileName"), str):
        path = obj.get("FilePath") if isinstance(obj.get("FilePath"), str) else ""
        script = "/".join(p.strip().rstrip("/") for p in (path, obj["FileName"]) if p.strip())
    command = get("Command")
    application = " / ".join(str(v).strip() for v in (get("Application"), get("SubApplication"))
                             if v not in (None, "")) or None
    job.description = _describe(
        obj.get("Description"), dummy=kind == "Job:Dummy", command=str(command) if command else None,
        script=script, extra_hints=hints, application=application,
        host=str(get("Host")).strip() if get("Host") else None, month_days=_month_days(when.get("MonthDays")),
        raw_days=raw_days, cyclic=isinstance(obj.get("Rerun"), dict) and "Every" in obj["Rerun"])
    return job


def _json_jobs(data: object) -> tuple[list[_CtmJob], list[_Flow]]:
    if not isinstance(data, dict):
        raise ValueError("Control-M JSON'u bir nesne ({…}) olmalı")
    defaults = data.get("Defaults") if isinstance(data.get("Defaults"), dict) else {}
    jobs: list[_CtmJob] = []
    flows: list[_Flow] = []

    def walk(obj: dict[str, Any], path: list[str]) -> None:
        if len(path) > MAX_DEPTH:
            return
        for key, value in obj.items():
            if not isinstance(value, dict) or key == "Defaults":
                continue
            kind = str(value.get("Type", ""))
            if kind.startswith("Job:"):
                jobs.append(_json_job(key, value, "/".join(path), defaults))
            elif kind in _FOLDER_TYPES:
                walk(value, [*path, key])
            elif kind == "Flow":
                sequence = value.get("Sequence") if isinstance(value.get("Sequence"), list) else []
                flows.append(_Flow("/".join(path), key, [str(s).strip() for s in sequence
                                                         if isinstance(s, str) and s.strip()], value))

    walk(data, [])
    return jobs, flows


# --------------------------------------------------------------------------- #
# Dosya → yapı
# --------------------------------------------------------------------------- #


def _is_json(text: str) -> bool:
    return text.lstrip().startswith(("{", "["))


def _parse(data: bytes) -> _Parsed:
    text = strip_bom(decode(data))
    if _is_json(text):
        try:
            loaded = json.loads(text)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise ValueError(f"JSON ayrıştırılamadı: {str(exc)[:200]}") from None
        jobs, flows = _json_jobs(loaded)
        xml = False
    else:
        root = parse_xml(data)
        tag = local(root.tag).upper()
        if tag not in _CONTAINERS | {"DEFTABLE", "JOB"}:
            raise ValueError(f"Control-M XML'i değil (kök öğe <{local(root.tag)}>)")
        jobs, flows, xml = _xml_jobs(root), [], True
    return _Parsed([job for job in jobs if job.name], flows, jobs, text, xml)


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #


def _build(parsed: _Parsed, builder: ModelBuilder, report: DocReport, file: str) -> None:
    def source(locator: str):
        return ref(file, locator, EXTRACTOR, confidence=1.0)

    keys: dict[int, str] = {}
    for job in parsed.jobs:
        keys[id(job)] = builder.process(job.name, source(job.locator), kind=ProcessKind.BATCH_JOB, tool=TOOL,
                                        description=job.description, schedule=job.schedule)
        report.processes += 1

    producers: dict[str, list[_CtmJob]] = {}
    conditional: dict[str, list[_CtmJob]] = {}
    for job in parsed.jobs:
        for name in job.adds:
            producers.setdefault(name, []).append(job)
        for name in job.conditional_adds:
            conditional.setdefault(name, []).append(job)

    added: set[tuple[str, str]] = set()

    def link(first: str, second: str, locator: str) -> None:
        builder.precedes(first, second, source(locator))
        if first != second and (first, second) not in added:
            added.add((first, second))
            report.flows += 1

    for job in parsed.jobs:
        external, other_day, only_conditional = [], [], []
        for cond in job.waits:
            if not cond.same_run:
                other_day.append(f"{cond.name} ({cond.date.strip()})")
                continue
            makers = [p for p in producers.get(cond.name, []) if p is not job]
            for maker in makers:
                link(keys[id(maker)], keys[id(job)], job.locator)
            if makers or job in producers.get(cond.name, []):
                continue
            if conditional.get(cond.name):
                only_conditional.append(cond.name)
            else:
                external.append(cond.name)
        if any(cond.op == "or" for cond in job.waits):
            report.note(f"{job.name}: koşullar VEYA (OR) ile bağlı ({' | '.join(c.name for c in job.waits)}); "
                        "her üreticiden sıra kenarı eklendi")
        if external:
            report.note(f"{job.name}: dış koşul (bu dosyada üreten job yok): {', '.join(external)}")
        if other_day:
            report.note(f"{job.name}: başka güne bağlı koşul sıra sayılmadı: {', '.join(other_day)}")
        if only_conditional:
            report.note(f"{job.name}: koşul yalnız ON/DOCOND ile (belirli bir sonuçta) ekleniyor, sıra "
                        f"sayılmadı: {', '.join(only_conditional)}")

    by_folder: dict[tuple[str, str], _CtmJob] = {(job.folder, job.name): job for job in parsed.jobs}
    by_name: dict[str, list[_CtmJob]] = {}
    for job in parsed.jobs:
        by_name.setdefault(job.name, []).append(job)
    for flow in parsed.flows:
        resolved: list[_CtmJob] = []
        for name in flow.sequence:
            job = by_folder.get((flow.folder, name)) or by_folder.get((flow.folder, name.split(":")[-1]))
            if job is None and len(by_name.get(name.split(":")[-1], [])) == 1:
                job = by_name[name.split(":")[-1]][0]
            if job is None:
                report.note(f"{flow.locator}: tanımsız job '{name}' atlandı")
                continue
            resolved.append(job)
        for first, second in zip(resolved, resolved[1:], strict=False):
            link(keys[id(first)], keys[id(second)], flow.locator)


def read_controlm(builder: ModelBuilder, report: DocReport, path: Path) -> list[tuple[str, str]]:
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
    if parsed.unnamed:
        report.note(f"{parsed.unnamed} job'ın adı (JOBNAME) yok; atlandı")
    if not parsed.jobs:
        report.status = "empty"
        report.note("dosyada Control-M job'ı bulunamadı")
        return []
    _build(parsed, builder, report, path.name)
    return []


def preview_controlm(path: Path) -> list[Block]:
    """Her job (ve JSON'daki her Flow) bir kod bloğu; konum okuyucudakiyle aynı (``klasör K · job J``)."""
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
    if parsed.xml:
        spans = element_spans(parsed.text, "JOB")
        exact = len(spans) == len(parsed.all_jobs)
        for index, job in enumerate(parsed.all_jobs):
            if not job.name:
                continue
            block: Block = {"locator": job.locator, "kind": "code"}
            if exact:
                start, end, line = spans[index]
                block.update(text=parsed.text[start:end].replace("\r\n", "\n"), line_start=line)
            else:
                block["text"] = xml_to_text(job.raw)
            blocks.append(block)
    else:
        for job in parsed.jobs:
            blocks.append({"locator": job.locator, "kind": "code",
                           "text": json.dumps({job.name: job.raw}, ensure_ascii=False, indent=2)})
        for flow in parsed.flows:
            blocks.append({"locator": flow.locator, "kind": "code",
                           "text": json.dumps({flow.name: flow.raw}, ensure_ascii=False, indent=2)})
    if not blocks:
        blocks.append({"locator": "", "kind": "note", "text": "Dosyada Control-M job'ı bulunamadı."})
    return blocks


def looks_like_controlm_xml(head: bytes, name: str) -> bool:
    text = sniff_text(head).lstrip()
    if not text.startswith("<") or "<DSExport" in text:
        return False
    return (re.search(r"<DEFTABLE[\s>]", text) is not None
            or (re.search(r"<JOB\s", text) is not None and "JOBNAME=" in text))


def looks_like_controlm_json(head: bytes, name: str) -> bool:
    text = sniff_text(head).lstrip()
    return text.startswith("{") and re.search(
        r'"Type"\s*:\s*"(?:Job:[\w:]+|Folder|SimpleFolder)"', text) is not None


register(FormatSpec(
    "controlm", "Control-M XML", (".xml",), read_controlm, preview_controlm, sniff=looks_like_controlm_xml,
    group="Zamanlayıcı", sample="ornek_bicimler/kart_controlm.xml",
    description="Control-M XML exportu: JOB → süreç, INCOND/OUTCOND → job sırası, TIMEFROM/WEEKDAYS",
), primary=False)
register(FormatSpec(
    "controlm_json", "Control-M JSON", (".json",), read_controlm, preview_controlm,
    sniff=looks_like_controlm_json, group="Zamanlayıcı", sample="ornek_bicimler/kart_controlm.json",
    description="Control-M Automation API JSON: Job:* → süreç, olaylar ve Flow → job sırası",
), primary=False)

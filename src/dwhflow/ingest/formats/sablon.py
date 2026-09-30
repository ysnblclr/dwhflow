"""Kuruma özel okuma şablonları: genel XML/JSON dışa aktarımları (ayar: ``okuma.sablonlar``).

Şablon biçimi (her öğe bir sözlük)::

    - ad: "Kurum ETL XML"      # görünen ad (zorunlu)
      dosya: "*.etl.xml"       # dosya adı deseni (fnmatch, büyük/küçük harf duyarsız; zorunlu)
      tur: xml                 # xml | json (zorunlu)
      kayit: ".//Mapping"      # kayıt yolu (zorunlu)
      kaynak: "Source/@name"   # kayda göre kaynak değer yolu (zorunlu)
      hedef: "Target/@name"    # kayda göre hedef değer yolu (zorunlu)
      surec: "@id"             # süreç/job adı yolu (isteğe bağlı)
      aciklama: "Description"  # açıklama yolu (isteğe bağlı)

**XML yolları** ElementTree yoludur (``.//Mapping``, ``Mappings/Mapping[@type='ETL']``); ad
alanları atılır (``ns:Mapping`` değil ``Mapping`` yazılır). Kayıt yolu kök öğeyi de içerebilir
(``/Export/Mapping``) ya da köke göre yazılabilir (``Mapping``). Değer yolu ``Öğe/@öznitelik``
(özniteliğin değeri), ``@öznitelik`` (kaydın kendi özniteliği) ya da ``Öğe`` (metni) biçimindedir;
birden çok öğe birden çok değer verir.

**JSON yolları** noktalı yoldur; ``[*]`` listenin tüm öğeleri, ``[n]`` n. öğe (``mappings[*]``,
``jobs[*].steps[*]``, ``$.data.items[*]``). Liste üzerinde anahtar istenirse her öğeye uygulanır
(``sources.name``).

Her değer ``,`` ``;`` ya da satır sonuyla bölünebilir. Her kayıt için kaynak × hedef çiftleri
``flow(kaynak, hedef, via=süreç)`` olur; kaynak ``sablon:<ad>``, konum ``kayıt n`` (birden çok
şablon aynı dosyaya uyarsa ``<ad> · kayıt n``).

**Seçim:** biçim yalnız **içerik tespitiyle** seçilir ve tespit katıdır: dosya adı bir şablonun
``dosya`` desenine uymalı, ``tur`` da uzantıya (``.xml``/``.json``; başka uzantıda içeriğe) uymalıdır.
Böylece şablon, kullanıcı açıkça desen tanımlamadıkça Control-M, DataStage, draw.io … dosyalarını
almaz. Geçersiz şablonlar okumayı durdurmaz; nedenleri okunan dokümanın raporuna yazılır.
"""

from __future__ import annotations

import fnmatch
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..base import DocReport, ModelBuilder, ref
from ..config import active
from ..preview import MAX_BLOCKS
from ..readers import _decode
from ..registry import SNIFF_BYTES, Block, FormatSpec, register
from ._ortak import (
    MAX_TEXT_FILE_BYTES,
    FormatError,
    Leftovers,
    fail,
    one_line,
    parse_xml,
    read_limited,
    strip_namespaces,
)

CONFIDENCE = 0.9
MAX_RECORDS = 100_000
_REQUIRED = ("ad", "dosya", "tur", "kayit", "kaynak", "hedef")
_OPTIONAL = ("surec", "aciklama")
_SPLIT = re.compile(r"\s*[,;\n]\s*")


@dataclass(frozen=True)
class Template:
    number: int
    name: str
    pattern: str
    kind: str  # xml | json
    record: str
    source: str
    target: str
    process: str | None = None
    description: str | None = None


# --------------------------------------------------------------------------- #
# Doğrulama
# --------------------------------------------------------------------------- #


def _check_path(kind: str, path: str) -> None:
    """Yolun sözdizimini okumadan önce denetler (değer yollarındaki ``/@öznitelik`` eki ayrılır)."""
    if kind == "json":
        json_steps(path)
        return
    element_path, attribute = _split_attribute(path)
    if attribute is not None and not re.fullmatch(r"[\w.:-]+", attribute):
        raise FormatError(f"geçersiz öznitelik adı '{attribute}' ('{path}')")
    if element_path in (".", ""):
        return
    try:
        ET.Element("_belge").findall(_xml_path(element_path))
    except (SyntaxError, KeyError, ValueError, TypeError) as exc:
        raise FormatError(f"geçersiz XML yolu '{path}': {exc}") from exc


def templates() -> tuple[list[Template], list[str]]:
    """(geçerli şablonlar, geçersizlerin açıklamaları) — etkin okuma ayarlarından."""
    valid: list[Template] = []
    problems: list[str] = []
    for number, raw in enumerate(active().templates, start=1):
        if not isinstance(raw, dict):
            problems.append(f"okuma şablonu {number} geçersiz: ad/dosya/tur/… alanları olan bir sözlük olmalı")
            continue
        title = str(raw.get("ad") or "").strip() or f"şablon {number}"
        missing = [key for key in _REQUIRED if not isinstance(raw.get(key), str) or not raw[key].strip()]
        wrong = [key for key in _OPTIONAL if raw.get(key) is not None and not isinstance(raw.get(key), str)]
        if missing or wrong:
            detail = "; ".join(filter(None, [f"eksik/geçersiz: {', '.join(missing)}" if missing else "",
                                             f"metin olmalı: {', '.join(wrong)}" if wrong else ""]))
            problems.append(f"okuma şablonu '{title}' atlandı ({detail})")
            continue
        kind = raw["tur"].strip().lower()
        if kind not in ("xml", "json"):
            problems.append(f"okuma şablonu '{title}' atlandı: tur 'xml' ya da 'json' olmalı ('{raw['tur']}')")
            continue
        template = Template(number, title, raw["dosya"].strip(), kind, raw["kayit"].strip(), raw["kaynak"].strip(),
                            raw["hedef"].strip(), (raw.get("surec") or "").strip() or None,
                            (raw.get("aciklama") or "").strip() or None)
        try:
            for path in (template.record, template.source, template.target, template.process, template.description):
                if path:
                    _check_path(kind, path)
        except FormatError as exc:
            problems.append(f"okuma şablonu '{title}' atlandı: {exc}")
            continue
        valid.append(template)
    return valid, problems


def _content_kind(head: bytes) -> str | None:
    start = head.lstrip(b"\xef\xbb\xbf \t\r\n")[:1]
    return "xml" if start == b"<" else "json" if start in (b"{", b"[") else None


def _kind_fits(template: Template, name: str, head: bytes) -> bool:
    suffix = Path(name).suffix.lower()
    if suffix in (".xml", ".json"):
        return template.kind == suffix[1:]
    return template.kind == _content_kind(head)


def matches(template: Template, name: str, head: bytes) -> bool:
    return fnmatch.fnmatchcase(name.lower(), template.pattern.lower()) and _kind_fits(template, name, head)


def looks_like_template(head: bytes, name: str) -> bool:
    valid, _ = templates()
    return any(matches(template, name, head) for template in valid)


# --------------------------------------------------------------------------- #
# XML
# --------------------------------------------------------------------------- #


def _xml_path(path: str) -> str:
    path = path.strip()
    if path.startswith("//"):
        return f".{path}"
    return path.lstrip("/") or "."


def _xml_records(root: ET.Element, path: str) -> list[ET.Element]:
    wrapper = ET.Element("_belge")
    wrapper.append(root)
    try:
        found = wrapper.findall(_xml_path(path))
        if not found and not path.strip().startswith((".", "/")):
            found = root.findall(path.strip())
    except (SyntaxError, KeyError, ValueError, TypeError) as exc:
        raise FormatError(f"geçersiz kayıt yolu '{path}': {exc}") from exc
    return [element for element in found if element is not wrapper]


def _split_attribute(path: str) -> tuple[str, str | None]:
    """``Source/@name`` → (``Source``, ``name``); ``@id`` → (``.``, ``id``); ``Source`` → (``Source``, None)."""
    path = path.strip()
    last = path.rsplit("/", 1)[-1]
    if last.startswith("@"):
        return path[: len(path) - len(last)].rstrip("/") or ".", last[1:].strip()
    return path, None


def _xml_values(record: ET.Element, path: str) -> list[str]:
    element_path, attribute = _split_attribute(path)
    try:
        elements = [record] if element_path in (".", "") else record.findall(element_path)
    except (SyntaxError, KeyError, ValueError, TypeError) as exc:
        raise FormatError(f"geçersiz değer yolu '{path}': {exc}") from exc
    if attribute is not None:
        return [value for value in (element.get(attribute) for element in elements) if value]
    return [text for text in ("".join(element.itertext()) for element in elements) if text.strip()]


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #

_SEGMENT = re.compile(r"([^\[\]]*)((?:\[(?:\*|\d+)\])*)")


def json_steps(path: str) -> list[str | int]:
    """``jobs[*].steps[*]`` → ``["jobs", "*", "steps", "*"]``; ``"*"`` tümü, ``int`` sıra."""
    value = path.strip()
    if value.startswith("$"):
        value = value[1:]
    value = value.lstrip(".")
    steps: list[str | int] = []
    if not value:
        return steps
    for segment in value.split("."):
        match = _SEGMENT.fullmatch(segment)
        if not match or not (match.group(1) or match.group(2)):
            raise FormatError(f"geçersiz JSON yolu '{path}'")
        if match.group(1):
            steps.append(match.group(1))
        for index in re.findall(r"\[(\*|\d+)\]", match.group(2)):
            steps.append("*" if index == "*" else int(index))
    return steps


def json_select(value: Any, steps: list[str | int]) -> list[Any]:
    current = [value]
    for step in steps:
        following: list[Any] = []
        for item in current:
            if step == "*":
                if isinstance(item, list):
                    following.extend(item)
                elif isinstance(item, dict):
                    following.extend(item.values())
            elif isinstance(step, int):
                if isinstance(item, list) and step < len(item):
                    following.append(item[step])
            elif isinstance(item, dict):
                if step in item:
                    following.append(item[step])
            elif isinstance(item, list):  # liste üzerinde anahtar: her öğeye uygula
                following.extend(sub[step] for sub in item if isinstance(sub, dict) and step in sub)
        current = following
    return current


def _json_records(document: Any, path: str) -> list[Any]:
    found = json_select(document, json_steps(path))
    if len(found) == 1 and isinstance(found[0], list):  # "mappings" ([*] yazılmamış)
        return list(found[0])
    return found


def _scalars(values: list[Any]) -> list[str]:
    out: list[str] = []
    for value in values:
        if isinstance(value, list):
            out.extend(_scalars(value))
        elif isinstance(value, bool):
            out.append(str(value).lower())
        elif isinstance(value, (str, int, float)):
            out.append(str(value))
    return out


# --------------------------------------------------------------------------- #
# Okuma
# --------------------------------------------------------------------------- #


def _names(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        for part in _SPLIT.split(value):
            part = one_line(part)
            if part and len(part) <= 256 and part not in out:
                out.append(part)
    return out


@dataclass
class _Record:
    template: Template
    locator: str
    sources: list[str]
    targets: list[str]
    process: str | None
    description: str | None
    snippet: str


def _load(template: Template, data: bytes, parsed: dict[str, Any]) -> Any:
    if template.kind not in parsed:
        if template.kind == "xml":
            parsed["xml"] = strip_namespaces(parse_xml(data))
        else:
            try:
                parsed["json"] = json.loads(_decode(data))
            except json.JSONDecodeError as exc:
                raise FormatError(f"JSON ayrıştırılamadı: {exc}") from exc
    return parsed[template.kind]


def _records(template: Template, document: Any, multi: bool) -> list[_Record]:
    if template.kind == "xml":
        raw = _xml_records(document, template.record)

        def values(record: Any, path: str) -> list[str]:
            return _xml_values(record, path)

        def snippet(record: Any) -> str:
            return ET.tostring(record, encoding="unicode")[:2000]
    else:
        raw = _json_records(document, template.record)

        def values(record: Any, path: str) -> list[str]:
            return _scalars(json_select(record, json_steps(path)))

        def snippet(record: Any) -> str:
            return json.dumps(record, ensure_ascii=False, indent=2)[:2000]
    out = []
    for number, record in enumerate(raw[:MAX_RECORDS], start=1):
        process = _names(values(record, template.process)) if template.process else []
        description = " ".join(values(record, template.description)) if template.description else ""
        out.append(_Record(template, f"{template.name} · kayıt {number}" if multi else f"kayıt {number}",
                           _names(values(record, template.source)), _names(values(record, template.target)),
                           process[0] if process else None, one_line(description)[:500] or None, snippet(record)))
    return out


def _selection(path: Path, report: DocReport | None) -> tuple[list[Template], list[str]]:
    valid, problems = templates()
    with path.open("rb") as handle:
        head = handle.read(SNIFF_BYTES)
    chosen = [template for template in valid if matches(template, path.name, head)]
    if not chosen:  # okuyucu zorlandı: türü uyan tüm şablonlar denenir
        chosen = [template for template in valid if template.kind == _content_kind(head)]
        if chosen and report is not None:
            report.note("dosya adı hiçbir şablon desenine uymadı; türü uyan şablonlar denendi: "
                        + ", ".join(template.name for template in chosen))
    return chosen, problems


def read_template(builder: ModelBuilder, report: DocReport, path: Path) -> Leftovers:
    chosen, problems = _selection(path, report)
    for problem in problems:
        report.note(problem)
    if not chosen:
        report.status = "unsupported"
        report.note("bu dosyaya uyan okuma şablonu yok (ayarlar → okuma.sablonlar)")
        return []
    try:
        data = read_limited(path, MAX_TEXT_FILE_BYTES)
    except FormatError as exc:
        return fail(report, str(exc))
    parsed: dict[str, Any] = {}
    applied = 0
    for template in chosen:
        try:
            records = _records(template, _load(template, data, parsed), len(chosen) > 1)
        except FormatError as exc:
            report.note(f"şablon '{template.name}': {exc}")
            continue
        applied += 1
        if not records:
            report.note(f"şablon '{template.name}': kayıt yolu '{template.record}' hiçbir öğe bulmadı")
            continue
        if len(records) >= MAX_RECORDS:
            report.note(f"şablon '{template.name}': ilk {MAX_RECORDS} kayıt okundu")
        _apply(builder, report, path.name, records)
    if not applied:
        report.status = "error"
    return []


def _apply(builder: ModelBuilder, report: DocReport, file: str, records: list[_Record]) -> None:
    empty = 0
    for record in records:
        if not (record.sources or record.targets):
            empty += 1
            continue
        evidence = f"{', '.join(record.sources) or '—'} → {', '.join(record.targets) or '—'}"
        if record.process:
            evidence += f" ({record.process})"
        if record.description:
            evidence += f" — {record.description}"
        source = ref(file, record.locator, f"sablon:{record.template.name}", confidence=CONFIDENCE, evidence=evidence)
        via = builder.process(record.process, source, description=record.description) if record.process else None
        sources = [builder.dataset(name, source) for name in record.sources]
        targets = [builder.dataset(name, source) for name in record.targets]
        for first in sources:
            for second in targets:
                if builder.flow(first, second, via=via, source=source):
                    report.flows += 1
        if via and not (sources and targets):
            for key in sources:
                builder.reads(key, via, source)
            for key in targets:
                builder.writes(via, key, source)
    if empty:
        report.note(f"şablon '{records[0].template.name}': {empty} kayıtta kaynak/hedef bulunamadı")


def preview_template(path: Path) -> list[Block]:
    chosen, problems = _selection(path, None)
    blocks: list[Block] = [{"locator": "", "kind": "note", "text": problem} for problem in problems]
    if not chosen:
        blocks.append({"locator": "", "kind": "note", "text": "Bu dosyaya uyan okuma şablonu yok."})
        return blocks
    data = read_limited(path, MAX_TEXT_FILE_BYTES)
    parsed: dict[str, Any] = {}
    for template in chosen:
        try:
            records = _records(template, _load(template, data, parsed), len(chosen) > 1)
        except FormatError as exc:
            blocks.append({"locator": "", "kind": "note", "text": f"Şablon '{template.name}': {exc}"})
            continue
        for record in records:
            blocks.append({"locator": record.locator, "kind": "code", "text": record.snippet})
            if len(blocks) >= MAX_BLOCKS:
                return blocks
    return blocks


register(FormatSpec("sablon", "Kurum şablonu", (".xml", ".json"), read_template, preview_template,
                    sniff=looks_like_template, group="ETL",
                    description="Ayarlardaki XML/JSON şablonları (okuma.sablonlar): kayıt → kaynak/hedef/süreç",
                    sample="ornek_sablon/kurum_etl.etl.xml"), primary=False)

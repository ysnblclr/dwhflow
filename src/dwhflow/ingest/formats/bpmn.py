"""BPMN 2.0 XML (``.bpmn`` ve içerikten tanınan ``.xml``): süreç akışı ve veri ilişkileri.

* Görevler (``task``, ``serviceTask``, ``scriptTask``, ``callActivity``, ``subProcess`` …) → süreç.
* ``sequenceFlow`` → görevler arası "önce gelir"; ara geçitler (``gateway``) ve olaylar
  (``event``) atlanarak görevden göreve bağlanır.
* ``dataObjectReference`` / ``dataStoreReference`` → veri nesnesi; görevin
  ``dataInputAssociation``'ı → okur, ``dataOutputAssociation``'ı → yazar.
* Her ``process`` bir bölümdür (``süreç n``; havuz adı varsa başlıkta gösterilir).
Görev/veri türü açık ipucudur; ad kuralı yalnız görünen adı temizlemek için kullanılır.
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree.ElementTree import Element

from ..base import DocReport, ModelBuilder
from ..registry import Block, FormatSpec, register
from . import _diyagram as diagram
from ._ortak import MAX_TEXT_FILE_BYTES, FormatError, Leftovers, fail, parse_xml, read_limited

_B = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
_TASKS = {"task", "userTask", "serviceTask", "scriptTask", "businessRuleTask", "sendTask", "receiveTask",
          "manualTask", "callActivity", "subProcess", "adHocSubProcess", "transaction"}
_HEAD = re.compile(rb"<(?:[\w.-]+:)?definitions\b")


def looks_like_bpmn(head: bytes, name: str) -> bool:
    return b"http://www.omg.org/spec/BPMN/20100524/MODEL" in head and bool(_HEAD.search(head))


def _refs(element: Element, tag: str) -> list[str]:
    return [(ref.text or "").strip() for ref in element.findall(f"{_B}{tag}") if (ref.text or "").strip()]


def _process_page(number: int, process: Element, title: str, stores: dict[str, str],
                  objects: dict[str, str]) -> diagram.Page:
    page = diagram.Page(prefix=f"süreç {number}", title=title)
    tasks: set[str] = set()
    following: dict[str, list[str]] = {}
    for element in process.iter():
        tag = element.tag[len(_B):] if isinstance(element.tag, str) and element.tag.startswith(_B) else ""
        element_id = element.get("id") or ""
        if not tag or not element_id:
            continue
        if tag in _TASKS:
            tasks.add(element_id)
            page.node(element_id, element.get("name") or element_id, diagram.PROCESS)
            for association in element.findall(f"{_B}dataInputAssociation"):
                for source in _refs(association, "sourceRef"):
                    page.links.append(diagram.Link(source, element_id))
            for association in element.findall(f"{_B}dataOutputAssociation"):
                for target in _refs(association, "targetRef"):
                    page.links.append(diagram.Link(element_id, target))
        elif tag == "dataObjectReference":
            name = element.get("name") or objects.get(element.get("dataObjectRef") or "") or element_id
            page.node(element_id, name, diagram.DATASET)
        elif tag == "dataStoreReference":
            name = element.get("name") or stores.get(element.get("dataStoreRef") or "") or element_id
            page.node(element_id, name, diagram.DATASET)
        elif tag == "sequenceFlow":
            source, target = element.get("sourceRef"), element.get("targetRef")
            if source and target:
                following.setdefault(source, []).append(target)
    # Görevden görevlere: geçit ve olaylar üzerinden ilerle.
    for task in sorted(tasks):
        seen: set[str] = set()
        stack = list(following.get(task, ()))
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            if current in tasks:
                page.links.append(diagram.Link(task, current))
            else:
                stack.extend(following.get(current, ()))
    return page


def bpmn_pages(path: Path) -> list[diagram.Page]:
    root = parse_xml(read_limited(path, MAX_TEXT_FILE_BYTES))
    if root.tag != f"{_B}definitions":
        raise FormatError("BPMN 2.0 dosyası değil (definitions kökü yok)")
    stores = {e.get("id") or "": e.get("name") or "" for e in root.iter(f"{_B}dataStore")}
    objects = {e.get("id") or "": e.get("name") or "" for e in root.iter(f"{_B}dataObject")}
    pools = {e.get("processRef") or "": e.get("name") or "" for e in root.iter(f"{_B}participant")}
    pages = []
    for number, process in enumerate(root.findall(f"{_B}process"), start=1):
        title = pools.get(process.get("id") or "") or process.get("name") or process.get("id") or ""
        pages.append(_process_page(number, process, title, stores, objects))
    if not pages:
        raise FormatError("BPMN dosyasında process öğesi yok")
    return pages


def read_bpmn(builder: ModelBuilder, report: DocReport, path: Path) -> Leftovers:
    try:
        pages = bpmn_pages(path)
    except FormatError as exc:
        return fail(report, f"BPMN okunamadı: {exc}")
    return diagram.apply(builder, report, path.name, pages, extractor="diyagram:bpmn", confidence=0.9)


def preview_bpmn(path: Path) -> list[Block]:
    return diagram.preview(bpmn_pages(path))


register(FormatSpec("bpmn", "BPMN 2.0", (".bpmn",), read_bpmn, preview_bpmn, sniff=looks_like_bpmn,
                    group="Diyagram", description="Görevler → süreç, sıra akışları → önce gelir, veri ilişkileri",
                    sample="ornek_bicimler/kart_sureci.bpmn"))

"""Archify tanılarından deterministik onarım.

Archify'ın kendi akışında bir ajan, ``finalize`` tanısını okuyup spec'i düzeltir
ve yeniden dener. DWHFlow'da ajan yok; yalnız motorun **makinece önerdiği**
düzeltmeler uygulanır. Şu an tek tür: kenar etiketi bir kutuyla ya da başka bir
etiketle çakışınca Archify'ın ``Suggested fix: set labelAt [x, y]`` önerisi.

Tahmin yapılmaz: öneri yoksa ya da etiketin hangi bağlantıya ait olduğu
belirsizse onarım yapılmaz ve diyagram başarısız raporlanır.
"""

from __future__ import annotations

import re
from typing import Any

_LABEL = re.compile(r'Label "(?P<label>[^"]+)"')
_COMPONENT = re.compile(r'component "(?P<component>[^"]+)"')
_LABEL_AT = re.compile(r"set labelAt \[(?P<x>-?\d+(?:\.\d+)?),\s*(?P<y>-?\d+(?:\.\d+)?)\]")


def label_fixes(diagnostics: list[dict[str, Any]]) -> list[tuple[str, str | None, list[float]]]:
    """(etiket, çakıştığı bileşen, önerilen konum) üçlüleri."""
    fixes = []
    for diagnostic in diagnostics:
        if diagnostic.get("code") != "layout/constraint":
            continue
        message = diagnostic.get("message") or ""
        label, point = _LABEL.search(message), _LABEL_AT.search(message)
        if not (label and point):
            continue
        component = _COMPONENT.search(message)
        fixes.append((label.group("label"), component.group("component") if component else None,
                      [float(point.group("x")), float(point.group("y"))]))
    return fixes


def apply_label_fixes(spec: dict[str, Any], diagnostics: list[dict[str, Any]]) -> int:
    """Önerileri spec'e uygular; uygulanan düzeltme sayısını döner."""
    applied = 0
    for label, component, point in label_fixes(diagnostics):
        candidates = [c for c in spec.get("connections", [])
                      if c.get("label") == label and "labelAt" not in c]
        if component and len(candidates) > 1:
            candidates = [c for c in candidates if component in (c.get("from"), c.get("to"))]
        if len(candidates) != 1:
            continue  # belirsiz: tahmin etme
        candidates[0]["labelAt"] = [int(round(v)) for v in point]
        applied += 1
    return applied

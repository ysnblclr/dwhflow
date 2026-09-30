"""Biçim testlerinin ortak yardımcıları (``test_formats_office`` ve ``test_formats_adversarial``)."""

from __future__ import annotations

import importlib.util
import io
import zipfile
from functools import cache
from pathlib import Path
from xml.sax.saxutils import escape

from dwhflow.ingest import IngestResult, preview_document
from dwhflow.model import EdgeKind
from dwhflow.render.slices import dataset_links

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "src" / "dwhflow" / "examples"
FORMATS = EXAMPLES / "ornek_bicimler"
HARD = EXAMPLES / "ornek_zor"
TEMPLATES = EXAMPLES / "ornek_sablon"


@cache
def generator():
    """``scripts/ornek_bicim_uret.py`` — ikili örnek üreticileri testlerde de kullanılır."""
    spec = importlib.util.spec_from_file_location("ornek_bicim_uret", ROOT / "scripts" / "ornek_bicim_uret.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def summary(result: IngestResult) -> dict:
    model = result.model
    names = {node_id: node.name for node_id, node in model.nodes.items()}
    return {
        "datasets": {d.name for d in model.datasets.values()},
        "processes": {p.name for p in model.processes.values()},
        "links": {(names[s], names[t]) for s, t in dataset_links(model)},
        "precedes": {(names[e.source], names[e.target]) for e in model.edges if e.kind is EdgeKind.PRECEDES},
    }


def docx_bytes(paragraphs: list[tuple[str | None, str]]) -> bytes:
    """En küçük .docx: (stil, metin) paragrafları."""
    body = "".join(
        "<w:p>" + (f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else "")
        + f"<w:r><w:t>{escape(text)}</w:t></w:r></w:p>" for style, text in paragraphs)
    document = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
                f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def source_locators(result: IngestResult, file: str) -> set[str]:
    """Modelde ``file`` dosyasından gelen bütün kaynak konumları."""
    model = result.model
    refs = [s for node in model.nodes.values() for s in node.sources]
    refs += [s for edge in model.edges for s in edge.sources]
    return {s.locator for s in refs if s.file == file and s.locator}


def assert_locators_in_preview(path: Path, result: IngestResult, config=None) -> None:
    """Okuyucunun yazdığı her konum önizlemede bir bloğa (ya da tablo bloğunun satırına) karşılık gelir."""
    _, blocks = preview_document(path, config)
    locators = {b["locator"] for b in blocks if b["locator"]}
    used = source_locators(result, path.name)
    assert used, "okuyucu konum yazmadı"
    missing = [u for u in used if u not in locators and not any(u.startswith(f"{b} · ") for b in locators)]
    assert not missing, (missing, sorted(locators))

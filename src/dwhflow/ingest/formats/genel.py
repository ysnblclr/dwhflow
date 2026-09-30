"""Genel dönüştürücü (yedek okuyucu): özel okuyucusu olmayan biçimler MarkItDown ile Markdown'a
çevrilir ve metin kurallarından geçer (``pip install "dwhflow[evrensel]"``).

Yalnız hiçbir biçim (uzantı ya da içerik tespiti) eşleşmediğinde devreye girer. Paket kurulu
değilse dosya "desteklenmiyor" raporlanır ve ileti ek paketi önerir. MarkItDown eklentileri
kapalıdır; dönüştürme yerel dosya üzerinde yapılır (ağ erişimi, LLM ya da OCR kullanılmaz).
"""

from __future__ import annotations

from pathlib import Path

from ..base import DocReport, ModelBuilder
from ..registry import Block, FormatSpec, register, set_fallback
from ..text import read_text
from ._ortak import Leftovers, module_available, text_blocks


def _available() -> bool:
    return module_available("markitdown")


def convert(path: Path) -> str:
    from markitdown import MarkItDown

    converter = MarkItDown()
    local = getattr(converter, "convert_local", None)
    result = local(str(path)) if callable(local) else converter.convert(str(path))
    text = getattr(result, "markdown", None) or getattr(result, "text_content", None) or ""
    return str(text)


def read_generic(builder: ModelBuilder, report: DocReport, path: Path) -> Leftovers:
    try:
        text = convert(path)
    except Exception as exc:  # noqa: BLE001 - dönüştürücü her türlü hata fırlatabilir
        report.status = "unsupported"
        report.note(f"genel dönüştürücü bu dosyayı okuyamadı: {type(exc).__name__}: {str(exc)[:200]}")
        return []
    if not text.strip():
        report.note("genel dönüştürücü metin üretmedi")
        return []
    result = read_text(text, builder, report, file=path.name)
    report.flows += result.flows
    return result.leftovers


def preview_generic(path: Path) -> list[Block]:
    return text_blocks(convert(path))


GENERIC = FormatSpec("genel", "Genel dönüştürücü", (), read_generic, preview_generic, extra="evrensel",
                     available=_available,
                     description="Özel okuyucusu olmayan biçimler MarkItDown ile metne çevrilir (isteğe bağlı)")
register(GENERIC)
set_fallback(GENERIC)

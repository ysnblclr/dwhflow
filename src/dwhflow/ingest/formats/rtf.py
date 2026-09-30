"""Zengin metin (``.rtf``): paragraflar, başlıklar ve tablolar (bkz. ``_rtf``; ek paket yok).

Konumlar Word ile aynı şemadadır: ``§ Başlık · paragraf n``, ``tablo n (§ Başlık)``.
"""

from __future__ import annotations

from pathlib import Path

from ..base import DocReport, ModelBuilder
from ..registry import Block, FormatSpec, register
from ._ortak import MAX_TEXT_FILE_BYTES, FormatError, Leftovers, fail, preview_items, read_items, read_limited
from ._rtf import rtf_items


def read_rtf(builder: ModelBuilder, report: DocReport, path: Path) -> Leftovers:
    try:
        items = rtf_items(read_limited(path, MAX_TEXT_FILE_BYTES))
    except FormatError as exc:
        return fail(report, f"RTF okunamadı: {exc}")
    return read_items(builder, report, path.name, items, table_reader="rtf_tablo")


def preview_rtf(path: Path) -> list[Block]:
    return preview_items(rtf_items(read_limited(path, MAX_TEXT_FILE_BYTES)))


register(FormatSpec("rtf", "RTF", (".rtf",), read_rtf, preview_rtf,
                    description="Zengin metin: paragraflar, başlıklar, tablolar (Türkçe kod sayfası)",
                    sample="ornek_bicimler/kart_notlari.rtf"))

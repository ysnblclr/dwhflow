"""HTML sayfaları (``.html``, ``.htm``, ``.xhtml``) ve web arşivleri (``.mht``, ``.mhtml``).

Confluence/wiki dışa aktarımları için: başlıklar konum olur (``§ Başlık · paragraf n``),
paragraflar ve liste maddeleri metin kurallarına, veri tabloları tablo yorumlayıcısına gider
(ayrıntı: ``_html``). Betikler ve stiller yok sayılır; hiçbir şey çalıştırılmaz ya da indirilmez.

**Güvenlik notu:** yüklenen dokümanlar arayüzde hiçbir zaman sayfa olarak açılmaz; yalnız
``Content-Disposition: attachment`` ve ``application/octet-stream`` ile indirilir ve önizleme
metin blokları olarak gösterilir (``tests/test_api_app.py`` bunu denetler). Bu yüzden HTML
uzantıları web'e doğrudan yüklenebilir. Dokümanları kendi kökeninden satır içi sunan bir uç
eklenirse bu biçim yeniden ``primary=False`` yapılmalıdır (depolanmış XSS).
"""

from __future__ import annotations

from pathlib import Path

from ..base import DocReport, ModelBuilder
from ..registry import Block, FormatSpec, register
from ._html import decode_html, html_items, looks_like_mime, mime_html
from ._ortak import MAX_TEXT_FILE_BYTES, FormatError, Item, Leftovers, fail, preview_items, read_items, read_limited

SUFFIXES = (".html", ".htm", ".xhtml", ".mht", ".mhtml")


def html_file_items(path: Path) -> list[Item]:
    data = read_limited(path, MAX_TEXT_FILE_BYTES)
    if looks_like_mime(data):
        markup = mime_html(data)
        if markup is None:
            raise FormatError("web arşivinde (MHTML) HTML bölümü yok")
        return html_items(markup)
    return html_items(decode_html(data))


def looks_like_html_file(head: bytes, name: str) -> bool:
    return name.lower().endswith(SUFFIXES)


def read_html(builder: ModelBuilder, report: DocReport, path: Path) -> Leftovers:
    try:
        items = html_file_items(path)
    except FormatError as exc:
        return fail(report, f"HTML okunamadı: {exc}")
    return read_items(builder, report, path.name, items, table_reader="html_tablo")


def preview_html(path: Path) -> list[Block]:
    return preview_items(html_file_items(path))


register(FormatSpec("html", "HTML", SUFFIXES, read_html, preview_html, sniff=looks_like_html_file,
                    description="Web sayfaları ve Confluence dışa aktarımları: başlıklar, paragraflar, tablolar",
                    sample="ornek_bicimler/kart_confluence.html"))

"""E-posta: ``.eml`` (standart kütüphane) ve ``.msg`` (``pip install "dwhflow[ofis]"`` ile).

Yüklenen e-posta bir **arşiv gibi açılır** (``_acma``): gövde, başında Konu/Kimden/Tarih
bilgisiyle ``<ad>.govde.txt`` (ya da HTML gövdeyse ``.html``) olarak, her ek kendi adıyla
ayrı doküman olarak okunur; raporda ``container`` e-postanın adıdır. Ekteki zip ve iletilen
e-postalar da (sınır içinde) açılır.

Aşağıdaki okuyucular yalnız e-posta açılmadan okunduğunda devreye girer (iç içe sınırı
aşan e-posta ya da zorlanan okuyucu): gövde okunur, ekler açılmaz ve bu raporda belirtilir.
``.msg`` için ``extract-msg`` kurulu değilse rapor ek paketi önerir.
"""

from __future__ import annotations

from pathlib import Path

from ..base import DocReport, ModelBuilder
from ..registry import Block, FormatSpec, register, register_expander
from ..text import read_text
from ._acma import expand_top, read_mail, render_mail
from ._html import html_items
from ._ortak import Leftovers, fail, module_available, preview_items, read_items, text_blocks


def _msg_available() -> bool:
    return module_available("extract_msg")


def read_mail_file(builder: ModelBuilder, report: DocReport, path: Path) -> Leftovers:
    try:
        mail = read_mail(path)
    except Exception as exc:  # noqa: BLE001 - bozuk e-posta yalnız kendini düşürsün
        return fail(report, f"e-posta okunamadı: {type(exc).__name__}: {str(exc)[:200]}")
    if mail.attachments:
        report.note(f"{len(mail.attachments)} ek bu okumada açılmadı (iç içe sınırı ya da zorlanan okuyucu); "
                    "ekleri ayrıca yükleyebilirsiniz")
    suffix, content = render_mail(mail)
    if suffix == ".html":
        return read_items(builder, report, path.name, html_items(content), table_reader="html_tablo")
    result = read_text(content, builder, report, file=path.name)
    report.flows += result.flows
    return result.leftovers


def preview_mail(path: Path) -> list[Block]:
    mail = read_mail(path)
    suffix, content = render_mail(mail)
    blocks = preview_items(html_items(content)) if suffix == ".html" else text_blocks(content)
    if mail.attachments:
        names = ", ".join(name for name, _ in mail.attachments)[:500]
        blocks.append({"locator": "", "kind": "note",
                       "text": f"Ekler ({len(mail.attachments)}): {names} — her biri ayrı doküman olarak okunur."})
    return blocks


register(FormatSpec("eposta", "E-posta (.eml)", (".eml",), read_mail_file, preview_mail, group="E-posta",
                    description="Gövde metin kurallarından geçer; ekler ayrı doküman olarak okunur",
                    sample="ornek_bicimler/kart_degisiklik.eml"))
register(FormatSpec("msg", "Outlook e-postası (.msg)", (".msg",), read_mail_file, preview_mail, group="E-posta",
                    description="Outlook .msg: gövde ve ekler (.eml gibi)", extra="ofis",
                    available=_msg_available))
register_expander((".eml",), expand_top)
if _msg_available():
    register_expander((".msg",), expand_top)

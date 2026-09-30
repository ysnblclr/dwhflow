"""Zip arşivleri: güvenli açma, iç içe arşivler ve e-postalar (ayrıntı ve sınırlar: ``_acma``).

Yüklenen ``.zip`` açılır ve içindeki her dosya ayrı doküman olarak okunur (``container``
arşivin adıdır). Aşağıdaki okuyucu yalnız **açılmamış** bir arşiv okunduğunda devreye
girer — iç içe derinlik sınırını aşan ya da bozuk/güvensiz bir iç arşiv — ve nedenini
raporlar; sessizce atlanan arşiv olmaz.
"""

from __future__ import annotations

from pathlib import Path

from ..base import DocReport, ModelBuilder
from ..registry import Block, FormatSpec, mark_unsupported, register, register_expander
from ._acma import MAX_DEPTH, ArsivHatasi, Budget, expand_top, open_archive, plan_zip
from ._ortak import Leftovers, fail


def _plan(path: Path) -> list[str]:
    with open_archive(path) as archive:
        return [name for _, name in plan_zip(archive, Budget())]


def read_archive(builder: ModelBuilder, report: DocReport, path: Path) -> Leftovers:
    try:
        names = _plan(path)
    except ArsivHatasi as exc:
        return fail(report, f"arşiv açılmadı: {exc}")
    report.status = "unsupported"
    report.note(f"iç içe arşiv açılmadı: arşivler en çok {MAX_DEPTH} düzey iç içe açılır "
                f"({len(names)} dosya içeriyor); içeriğini ayrıca yükleyin")
    return []


def preview_archive(path: Path) -> list[Block]:
    try:
        names = _plan(path)
    except ArsivHatasi as exc:
        return [{"locator": "", "kind": "note", "text": f"Arşiv açılamaz: {exc}"}]
    listing = ", ".join(names[:200]) + (" …" if len(names) > 200 else "")
    return [{"locator": "", "kind": "note",
             "text": f"Arşivde {len(names)} dosya var; her biri ayrı doküman olarak okunur: {listing}"}]


register(FormatSpec("arsiv", "Zip arşivi", (".zip",), read_archive, preview_archive, group="Arşiv",
                    description=f"İçindeki dosyalar ayrı okunur; iç içe {MAX_DEPTH} düzey, boyut ve sayı sınırlı",
                    sample="ornek_bicimler/kart_paket.zip"))
register_expander((".zip",), expand_top)
for _suffix, _label in ((".rar", "RAR"), (".7z", "7-Zip"), (".tar", "tar"), (".gz", "gzip"), (".tgz", "tar.gz")):
    mark_unsupported(_suffix, f"{_label} arşivleri açılamıyor; zip olarak sıkıştırıp yükleyin.")

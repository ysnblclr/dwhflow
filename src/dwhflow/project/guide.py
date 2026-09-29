"""Biçim rehberi (US-0.6): hangi başlıkların, ad kurallarının ve cümle kalıplarının tanındığı.

Rehber sabit bir metin değildir; **etkin ayarlardan** üretilir. Ayarlara eklenen bir
eş anlamlı ya da ad kuralı rehberde anında görünür.
"""

from __future__ import annotations

from typing import Any

from ..ingest.config import DEFAULT_ROLES, ROLE_LABELS, IngestConfig
from ..ingest.registry import REGISTRY, catalog

SENTENCE_PATTERNS: list[dict[str, str]] = [
    {"pattern": "<kaynak>'dan/tablosundan <hedef>'e/tablosuna … yüklenir / aktarılır / taşınır / yazılır",
     "example": "STG_MUSTERI tablosundan ODS_MUSTERI tablosuna aktarılır."},
    {"pattern": "<hedef>, <kaynak>'dan beslenir / türetilir / oluşturulur",
     "example": "DWH.D_MUSTERI, ODS_MUSTERI tablosundan beslenir."},
    {"pattern": "<job> job'ı / prosedürüyle / script'iyle … (akışı yapan süreç)",
     "example": "STG_MUSTERI verisi SP_ODS_MUSTERI prosedürüyle ODS_MUSTERI tablosuna aktarılır."},
    {"pattern": "<ad> sistemine / portalına … gönderilir (hedef dış sistem)",
     "example": "EXP_BDDK_KART dosyası her sabah BDDK_PORTAL sistemine gönderilir."},
    {"pattern": "<ad> dosyası / dizinine … (dosya nesnesi)",
     "example": "SRC_KART_ISLEM verisi KART_ISLEM_YYYYMMDD.dat dosyasına yazılır."},
    {"pattern": "Oklu zincir: A -> B -> C (ya da →, =>)",
     "example": "SRC_MUSTERI -> STG_MUSTERI -> ODS_MUSTERI -> DWH.D_MUSTERI"},
    {"pattern": "İngilizce: from A into B · A is loaded into B · B is populated from A · A feeds B",
     "example": "STG_ORDERS is loaded into ODS_ORDERS."},
]

TIPS = [
    "Tablo adlarında alt çizgi, şema noktası ya da dosya uzantısı olmalı (BDDK gibi kısaltmalar tablo sayılmaz).",
    "Bir cümlede tek akış anlatın; yön eklerini (-dan/-den kaynak, -a/-e hedef) kullanın.",
    "Olumsuz cümleler (\"yüklenmez\") akış olarak alınmaz.",
    "Kuralla çözülemeyen paragraflar onay kuyruğunda \"insan bakmalı\" olarak listelenir; LLM açıksa ona gönderilir.",
]


def build(config: IngestConfig) -> dict[str, Any]:
    roles = []
    for role, synonyms in config.roles.items():
        builtin = set(DEFAULT_ROLES.get(role, ()))
        roles.append({"role": role, "label": ROLE_LABELS.get(role, role),
                      "synonyms": [{"text": s, "custom": s not in builtin} for s in synonyms]})
    rules = [{"type": r.type, "value": r.value, "layer": r.layer.value,
              "kind": r.kind.value if r.kind else None, "origin": r.origin, "label": r.describe()}
             for r in config.rules]
    return {
        "roles": roles, "rules": rules, "patterns": SENTENCE_PATTERNS, "tips": TIPS,
        "formats": catalog(), "unsupported": [{"suffix": k, "message": v}
                                              for k, v in sorted(REGISTRY.unsupported.items())],
        "systems": sorted(set(config.systems.values())),
    }


def to_markdown(guide: dict[str, Any], title: str = "DWHFlow biçim rehberi") -> str:
    lines = [f"# {title}", "", "## Tablo başlıkları (STTM, job listeleri)", "",
             "Bir tablonun satırları, başlıkları aşağıdaki rollerden birine eşleşirse okunur. "
             "Eşleşme büyük/küçük harf ve Türkçe karakterden bağımsızdır.", "",
             "| Rol | Tanınan başlıklar |", "|---|---|"]
    for role in guide["roles"]:
        synonyms = ", ".join(f"**{s['text']}**" if s["custom"] else s["text"] for s in role["synonyms"])
        lines.append(f"| {role['label']} | {synonyms} |")
    lines += ["", "Kalın yazılanlar bu projenin ayarlarından gelir.", "",
              "## Ad → katman kuralları", "", "Sırayla denenir; ilk eşleşen kazanır.", "",
              "| Kural | Katman | Tür | Kaynak |", "|---|---|---|---|"]
    for rule in guide["rules"]:
        lines.append(f"| {rule['label']} | {rule['layer']} | {rule['kind'] or '—'} | {rule['origin']} |")
    lines += ["", "## Cümle kalıpları", ""]
    for pattern in guide["patterns"]:
        lines += [f"- {pattern['pattern']}", f"  - Örnek: *{pattern['example']}*"]
    lines += ["", "## İpuçları", ""] + [f"- {tip}" for tip in guide["tips"]]
    lines += ["", "## Okunan biçimler", "", "| Biçim | Uzantılar | Açıklama |", "|---|---|---|"]
    for item in guide["formats"]:
        suffixes = ", ".join(item["suffixes"]) or "içerikten tanınır"
        extra = f" (ek paket: {item['extra']})" if item.get("extra") else ""
        lines.append(f"| {item['label']} | {suffixes} | {item['description']}{extra} |")
    return "\n".join(lines) + "\n"

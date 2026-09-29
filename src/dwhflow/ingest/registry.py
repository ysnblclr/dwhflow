"""Biçim kayıt sistemi: hangi dosya hangi okuyucuyla okunur.

Yeni bir biçim eklemek için ``ingest/formats/`` altına bir modül koyup ``register``
çağırmak yeterlidir; paket içindeki modüller otomatik yüklenir. Paylaşılan hiçbir
dosyaya dokunmak gerekmez::

    from ..registry import FormatSpec, register

    register(FormatSpec(key="controlm", label="Control-M", suffixes=(".xml",),
                        reader=read_controlm, sniff=looks_like_controlm,
                        group="Zamanlayıcı", description="Control-M XML dışa aktarımı"))

Okuyucu seçimi (``resolve``) sırası:

1. Ayarlarda dosyaya zorlanan okuyucu (``okuma.okuyucu_zorla``).
2. Uzantısı genel (``.txt``, ``.xml``, ``.json``, ``.py``) ya da bilinmeyen dosyalarda
   **içerik tespiti**: kayıtlı ``sniff`` işlevleri kayıt sırasıyla denenir.
3. Uzantı eşlemesi.
4. Yedek okuyucu (ör. isteğe bağlı genel dönüştürücü).
5. Hiçbiri yoksa: desteklenmiyor + ne yapılabileceği.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .base import DocReport, ModelBuilder
from .config import IngestConfig

Leftovers = list[tuple[str, str]]
Reader = Callable[[ModelBuilder, DocReport, Path], Leftovers]
#: Önizleme bloğu: {"locator", "kind": heading|paragraph|table|code|note, "text",
#: "headers"?, "rows"?, "line_start"?}
Block = dict[str, Any]
Previewer = Callable[[Path], list[Block]]
#: (dosyanın ilk 64 KB'ı, dosya adı) → bu biçim mi?
Sniffer = Callable[[bytes, str], bool]
#: (arşiv, boş çalışma dizini) → açılan dosyalar
Expander = Callable[[Path, Path], list[Path]]

SNIFF_BYTES = 64 * 1024
GENERIC_SUFFIXES = frozenset({".txt", ".xml", ".json", ".py", ""})
GROUPS = ("Doküman", "Tablo", "Kod", "ETL", "Zamanlayıcı", "Diyagram", "E-posta", "Arşiv", "Model")


@dataclass
class FormatSpec:
    key: str
    label: str
    suffixes: tuple[str, ...]
    reader: Reader
    preview: Previewer | None = None
    sniff: Sniffer | None = None
    group: str = "Doküman"
    description: str = ""
    #: Gerekli isteğe bağlı ek paket adı (ör. "ofis"); ``available`` False dönerse okunmaz.
    extra: str | None = None
    available: Callable[[], bool] | None = None
    #: Biçimin örnek dosyası (``examples/`` altındaki göreli yol), biçim rehberi için.
    sample: str | None = None

    def is_available(self) -> bool:
        try:
            return self.available() if self.available else True
        except Exception:  # noqa: BLE001 - bağımlılık denetimi hiçbir zaman okumayı düşürmesin
            return False

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "label": self.label, "suffixes": list(self.suffixes),
            "group": self.group, "description": self.description, "extra": self.extra,
            "available": self.is_available(), "sniffs": self.sniff is not None,
            "preview": self.preview is not None, "sample": self.sample,
        }


@dataclass
class Registry:
    formats: dict[str, FormatSpec] = field(default_factory=dict)
    by_suffix: dict[str, str] = field(default_factory=dict)
    expanders: dict[str, Expander] = field(default_factory=dict)
    fallback: FormatSpec | None = None
    #: Tanınan ama okunamayan uzantılar için kullanıcıya gösterilecek açıklama.
    unsupported: dict[str, str] = field(default_factory=dict)


REGISTRY = Registry()


def register(spec: FormatSpec, *, primary: bool = True) -> FormatSpec:
    """Biçimi kaydeder.

    ``primary=True``: ``suffixes`` bu biçime aittir; aynı uzantıyı iki biçim sahiplenemez.
    ``primary=False``: uzantılar yalnız bilgi amaçlıdır; biçim **yalnız içerik tespitiyle**
    seçilir (ör. ``.xml`` uzantılı Control-M ve DataStage exportları).
    """
    if spec.key in REGISTRY.formats:
        raise ValueError(f"biçim anahtarı iki kez kaydedildi: {spec.key}")
    if not primary and spec.sniff is None:
        raise ValueError(f"{spec.key}: primary=False biçimin sniff işlevi olmalı")
    claimed = [s.lower() for s in spec.suffixes] if primary else []
    for suffix in claimed:
        owner = REGISTRY.by_suffix.get(suffix)
        if owner is not None:
            raise ValueError(f"'{suffix}' uzantısı zaten '{owner}' biçimine ait")
    REGISTRY.formats[spec.key] = spec
    for suffix in claimed:
        REGISTRY.by_suffix[suffix] = spec.key
        REGISTRY.unsupported.pop(suffix, None)
    return spec


def register_expander(suffixes: tuple[str, ...], expander: Expander) -> None:
    for suffix in suffixes:
        REGISTRY.expanders[suffix.lower()] = expander
        REGISTRY.unsupported.pop(suffix.lower(), None)


def set_fallback(spec: FormatSpec) -> None:
    REGISTRY.fallback = spec


def mark_unsupported(suffix: str, message: str) -> None:
    """Okunamayan ama tanınan bir uzantıyı açıklamasıyla kaydeder (kayıtlı biçimi ezmez)."""
    suffix = suffix.lower()
    if suffix not in REGISTRY.by_suffix and suffix not in REGISTRY.expanders:
        REGISTRY.unsupported[suffix] = message


def spec_for(name_or_suffix: str) -> FormatSpec | None:
    value = name_or_suffix.lower()
    if value in REGISTRY.formats:
        return REGISTRY.formats[value]
    key = REGISTRY.by_suffix.get(value if value.startswith(".") else f".{value}")
    return REGISTRY.formats.get(key) if key else None


def _head(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            return handle.read(SNIFF_BYTES)
    except OSError:
        return b""


def sniff(path: Path) -> FormatSpec | None:
    head = _head(path)
    if not head:
        return None
    for spec in REGISTRY.formats.values():
        if spec.sniff is None:
            continue
        try:
            if spec.sniff(head, path.name):
                return spec
        except Exception:  # noqa: BLE001 - hatalı bir sniffer diğerlerini engellemesin
            continue
    return None


def resolve(path: Path, config: IngestConfig) -> tuple[FormatSpec | None, str, str]:
    """(biçim, nasıl seçildi, açıklama). Biçim yoksa açıklama kullanıcıya gösterilir."""
    forced = config.forced_readers.get(path.name)
    if forced:
        spec = spec_for(forced)
        if spec is not None:
            return spec, "zorla", f"ayarlarda '{spec.label}' okuyucusu zorlandı"
    suffix = path.suffix.lower()
    if suffix in GENERIC_SUFFIXES or suffix not in REGISTRY.by_suffix:
        spec = sniff(path)
        if spec is not None:
            return spec, "icerik", f"içerikten '{spec.label}' olarak tanındı"
    if suffix in REGISTRY.by_suffix:
        return REGISTRY.formats[REGISTRY.by_suffix[suffix]], "uzanti", ""
    if REGISTRY.fallback is not None and REGISTRY.fallback.is_available():
        return REGISTRY.fallback, "yedek", "özel okuyucu yok; genel dönüştürücüyle metin olarak okundu"
    message = REGISTRY.unsupported.get(suffix) or f"'{suffix or '(uzantısız)'}' türü desteklenmiyor"
    if REGISTRY.fallback is not None and REGISTRY.fallback.extra:
        message += f" (genel dönüştürücü için: pip install \"dwhflow[{REGISTRY.fallback.extra}]\")"
    return None, "yok", message


def accepted_suffixes() -> list[str]:
    """Yüklemede kabul edilen uzantılar (okunamayanlar da kabul edilir ki raporlansın)."""
    return sorted(set(REGISTRY.by_suffix) | set(REGISTRY.expanders) | set(REGISTRY.unsupported)
                  - {""})


def catalog() -> list[dict[str, Any]]:
    """Biçim rehberi ve ayarlar ekranı için kayıtlı biçimler."""
    items = [spec.as_dict() for spec in REGISTRY.formats.values()]
    items.sort(key=lambda item: (GROUPS.index(item["group"]) if item["group"] in GROUPS else 99,
                                 item["label"]))
    return items

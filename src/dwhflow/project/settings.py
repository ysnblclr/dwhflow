"""Katmanlı ayarlar: paket varsayılanı < genel (çalışma alanı) < proje.

Ayar dosyaları YAML'dır ve **yalnız değişen anahtarları** tutar; etkin ayar üç
katmanın derin birleşimidir (sözlükler birleşir, liste ve değerler üst katmanda
tamamen değişir). Her yaprak değer için nereden geldiği (``varsayilan``, ``genel``,
``proje``) hesaplanır; ayarlar ekranı bunu gösterir.

Parola ve API anahtarı hiçbir katmanda tutulmaz: LLM profilinde yalnız anahtarın
bulunduğu ortam değişkeninin adı vardır.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ..ingest.config import DEFAULT_ROLES, IngestConfig
from ..llm import LLMConfig
from ..model import DatasetKind, Layer


class SettingsError(ValueError):
    """Ayar doğrulanamadı; mesaj kullanıcıya gösterilir."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NameRuleSetting(_Strict):
    tur: Literal["onek", "sonek", "regex", "tam"]
    deger: str = Field(min_length=1, max_length=200)
    katman: Layer
    nesne_turu: DatasetKind | None = None

    @model_validator(mode="after")
    def _regex(self) -> NameRuleSetting:
        if self.tur == "regex":
            try:
                re.compile(self.deger)
            except re.error as exc:
                raise ValueError(f"geçersiz düzenli ifade: {exc}") from exc
        return self


class TemplateSetting(_Strict):
    ad: str = Field(min_length=1, max_length=80)
    dosya: str = Field(min_length=1, max_length=200, description="dosya adı deseni (fnmatch)")
    tur: Literal["xml", "json"]
    kayit: str = Field(min_length=1, max_length=300)
    kaynak: str = Field(min_length=1, max_length=300)
    hedef: str = Field(min_length=1, max_length=300)
    surec: str | None = Field(default=None, max_length=300)
    aciklama: str | None = Field(default=None, max_length=300)


class ReadingSettings(_Strict):
    #: rol → ek başlık eş anlamlıları (yerleşik listelere eklenir)
    es_anlamlilar: dict[str, list[str]] = Field(default_factory=dict)
    #: ad → katman kuralları; yerleşik kurallardan ÖNCE denenir
    ad_kurallari: list[NameRuleSetting] = Field(default_factory=list)
    varsayilan_ad_kurallari: bool = True
    #: kanonik sistem adı → eş anlamlılar
    sistemler: dict[str, list[str]] = Field(default_factory=dict)
    #: dosya adı → biçim anahtarı ya da uzantı
    okuyucu_zorla: dict[str, str] = Field(default_factory=dict)
    sablonlar: list[TemplateSetting] = Field(default_factory=list)

    @field_validator("es_anlamlilar")
    @classmethod
    def _roles(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        unknown = sorted(set(value) - set(DEFAULT_ROLES))
        if unknown:
            raise ValueError(f"bilinmeyen sütun rolü: {', '.join(unknown)}")
        return {role: [s.strip() for s in synonyms if s and s.strip()] for role, synonyms in value.items()}

    @field_validator("okuyucu_zorla")
    @classmethod
    def _forced(cls, value: dict[str, str]) -> dict[str, str]:
        from ..ingest.registry import spec_for

        bad = sorted(name for name, reader in value.items() if spec_for(reader) is None)
        if bad:
            raise ValueError(f"tanınmayan okuyucu: {', '.join(f'{n} → {value[n]}' for n in bad)}")
        return value


class FocusSetting(_Strict):
    nesne: str = Field(min_length=1)
    adim: int = Field(default=2, ge=1, le=4)


class AutoDiagrams(_Strict):
    genel: bool = True
    soy: bool = True
    batch: bool = True


class DiagramSettings(_Strict):
    butce: int = Field(default=24, ge=8, le=40, description="diyagram başına en çok tablo")
    job_butcesi: int = Field(default=30, ge=8, le=60)
    kalite: Literal["showcase", "standard"] = "showcase"
    paralellik: int = Field(default=2, ge=1, le=8)
    otomatik: AutoDiagrams = Field(default_factory=AutoDiagrams)
    yollar: list[tuple[str, str]] = Field(default_factory=list)
    cevreler: list[FocusSetting] = Field(default_factory=list)
    gizli: list[str] = Field(default_factory=list)


class LLMProfile(_Strict):
    base_url: str = "http://localhost:11434/v1"
    model: str = "qwen3:8b"
    api_key_env: str | None = "DWHFLOW_AI_KEY"
    timeout_s: float = Field(default=180.0, gt=0, le=1800)
    temperature: float = Field(default=0.0, ge=0, le=2)
    allow_external_api: bool = False
    max_chars_per_call: int = Field(default=3500, ge=500, le=60000)

    @field_validator("base_url")
    @classmethod
    def _url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("adres http:// ya da https:// ile başlamalı")
        return value.rstrip("/")

    @field_validator("api_key_env")
    @classmethod
    def _env_name(cls, value: str | None) -> str | None:
        if value and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", value):
            # Anahtarın kendisinin yanlışlıkla buraya yapıştırılmasını da engeller.
            raise ValueError("ortam değişkeni adı olmalı (ör. DWHFLOW_AI_KEY), anahtarın kendisi değil")
        return value


class LLMSettings(_Strict):
    etkin: bool = False
    profil: str = "yerel"
    profiller: dict[str, LLMProfile] = Field(default_factory=lambda: {"yerel": LLMProfile()})
    guven_esigi: float = Field(default=0.0, ge=0, le=1)
    maske: bool = True
    tablo_eslemesi: bool = True

    @model_validator(mode="after")
    def _profile_exists(self) -> LLMSettings:
        if self.profil not in self.profiller:
            raise ValueError(f"'{self.profil}' adında bir LLM profili yok")
        return self


class Settings(_Strict):
    okuma: ReadingSettings = Field(default_factory=ReadingSettings)
    diyagram: DiagramSettings = Field(default_factory=DiagramSettings)
    yz: LLMSettings = Field(default_factory=LLMSettings)

    # -- türetilen yapılandırmalar ----------------------------------------- #

    def ingest_config(self) -> IngestConfig:
        return IngestConfig.from_settings(self.okuma.model_dump(mode="json"))

    def llm_config(self, profile: str | None = None) -> LLMConfig:
        name = profile or self.yz.profil
        if name not in self.yz.profiller:
            raise SettingsError(f"'{name}' adında bir LLM profili yok")
        data = self.yz.profiller[name].model_dump()
        return LLMConfig(enabled=self.yz.etkin, **{k: v for k, v in data.items() if v is not None})


DEFAULTS: dict[str, Any] = Settings().model_dump(mode="json")
LEVELS = ("varsayilan", "genel", "proje")


# --------------------------------------------------------------------------- #
# Birleştirme
# --------------------------------------------------------------------------- #


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def apply_patch(level: dict[str, Any], patch: dict[str, Any], _prefix: str = "") -> dict[str, Any]:
    """Bir katmana yama uygular. ``None`` değeri anahtarı katmandan siler (alttaki katmana döner).

    Ayar bölümleri birleşir; anahtarları kullanıcı verisi olan sözlükler (eş anlamlılar,
    sistemler, okuyucu zorlama, LLM profilleri) ise **bütün olarak** değişir — yoksa
    listeden çıkarılan bir öğe dosyada kalırdı.
    """
    out = copy.deepcopy(level or {})
    for key, value in (patch or {}).items():
        path = f"{_prefix}{key}"
        if value is None:
            out.pop(key, None)
        elif path in _MAPPING_LEAVES:
            out[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            merged = apply_patch(out[key], value, f"{path}.")
            if merged:
                out[key] = merged
            else:
                out.pop(key)
        elif isinstance(value, dict):
            cleaned = apply_patch({}, value, f"{path}.")
            if cleaned:
                out[key] = cleaned
        else:
            out[key] = copy.deepcopy(value)
    return out


def _leaves(data: Any, prefix: str = "") -> dict[str, Any]:
    if not isinstance(data, dict) or not data:
        return {prefix: data} if prefix else {}
    out: dict[str, Any] = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and value and not _is_mapping_leaf(path):
            out.update(_leaves(value, path))
        else:
            out[path] = value
    return out


#: Anahtarları kullanıcı verisi olan sözlükler bir bütün (yaprak) sayılır.
_MAPPING_LEAVES = {"okuma.es_anlamlilar", "okuma.sistemler", "okuma.okuyucu_zorla", "yz.profiller"}


def _is_mapping_leaf(path: str) -> bool:
    return path in _MAPPING_LEAVES


def _format_error(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors()[:6]:
        where = ".".join(str(p) for p in error["loc"])
        parts.append(f"{where}: {error['msg']}")
    return "; ".join(parts)


def validate(data: dict[str, Any]) -> Settings:
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        raise SettingsError(_format_error(exc)) from exc


def effective(global_level: dict[str, Any] | None, project_level: dict[str, Any] | None = None) -> Settings:
    return validate(deep_merge(deep_merge(DEFAULTS, global_level or {}), project_level or {}))


def with_origins(global_level: dict[str, Any] | None,
                 project_level: dict[str, Any] | None = None) -> dict[str, Any]:
    """Etkin ayar + her yaprağın kaynağı: ``{"values": {...}, "origins": {"diyagram.butce": "proje"}}``."""
    settings = effective(global_level, project_level)
    values = settings.model_dump(mode="json")
    global_leaves = _leaves(global_level or {})
    project_leaves = _leaves(project_level or {})
    origins = {}
    for path in _leaves(values):
        if path in project_leaves or any(path.startswith(p + ".") for p in project_leaves):
            origins[path] = "proje"
        elif path in global_leaves or any(path.startswith(p + ".") for p in global_leaves):
            origins[path] = "genel"
        else:
            origins[path] = "varsayilan"
    return {"values": values, "origins": origins}

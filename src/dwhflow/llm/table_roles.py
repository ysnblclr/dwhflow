"""Tanınmayan tablo başlıklarını LLM ile eşleme (US-6.2, DAF ``dwhflow.table-roles/1``).

Küçük modeller satır satır tablo okumada hata yapar; bu yüzden LLM'e yalnız **başlıklar ve
ilk 3 satır** gider, model sütunların **rolünü** söyler, satırları kural tabanlı okuyucu okur.

Model yanıtı kural tabanlı denetlenir:

* her anahtar verilen başlıklardan biriyle birebir aynı olmalı (baş/son boşluk hariç),
* her değer okuyucunun bildiği bir rol olmalı (``ingest.config.DEFAULT_ROLES``),
* bir rol birden çok başlığa verilemez (belirsiz olduğu için o rolün bütün atamaları düşer),
* ``table_kind`` geçerli olmalı,
* eşleme okuyucunun tabloyu tanıyabileceği kadar rol içermeli: eşleme (STTM) için kaynak ve
  hedef tablo; job listesi için job ile birlikte önceki job ya da bir tablo rolü.

Kullanıcı onaylarsa ``roles_to_synonyms`` çıktısı ayarların ``okuma.es_anlamlilar``
bölümüne eklenir; aynı başlık bir daha tanınmayan tablo olmaz, LLM'e de gitmez.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..daf import TABLE_KINDS, TableRoles
from ..ingest.base import fold
from ..ingest.config import DEFAULT_ROLES, ROLE_LABELS
from . import prompts as prompt_library
from .client import LLMClient, LLMError, parse_json_reply
from .extract import _attr, neutralize
from .mask import add_counts, mask_text, should_mask

PROMPT_ID = "table_roles"
#: LLM'e giden en çok örnek satır ve hücre uzunluğu.
MAX_ROWS = 3
MAX_CELL = 120
#: ``suggest_for_unrecognized``'ın bir seferde LLM'e soracağı en çok farklı başlık dizisi.
MAX_TABLES = 50


@dataclass
class TableRoleSuggestion:
    """Bir tablonun başlık eşleme önerisi ve denetim sonucu."""

    #: Denetimden geçen eşleme (geçersiz atamalar düşülmüş); yanıt kullanılamazsa None.
    table_roles: TableRoles | None = None
    #: Eşleme okuyucunun tabloyu tanımasına yetiyor mu (onaylanabilir mi)?
    usable: bool = False
    #: Kullanıcıya gösterilecek red/uyarı nedenleri.
    reasons: list[str] = field(default_factory=list)
    #: Düşürülen atamalar: {"header", "role", "reason"}
    rejected: list[dict[str, Any]] = field(default_factory=list)
    raw: str | None = None
    parsed: dict[str, Any] | None = None
    error: str | None = None
    #: {"id", "version", "origin"}
    prompt: dict[str, Any] = field(default_factory=dict)
    #: LLM'e giden kullanıcı mesajı (maskeli)
    sent: str | None = None
    masked: dict[str, int] = field(default_factory=dict)
    duration_ms: int = 0

    @property
    def synonyms(self) -> dict[str, list[str]]:
        """Onaylanırsa okuma kurallarına eklenecek eş anlamlılar (kullanılamazsa boş)."""
        if not (self.usable and self.table_roles):
            return {}
        return roles_to_synonyms(self.table_roles)

    def as_dict(self) -> dict[str, Any]:
        return {
            "suggestion": self.table_roles.model_dump(mode="json", by_alias=True) if self.table_roles else None,
            "usable": self.usable, "synonyms": self.synonyms, "reasons": list(self.reasons),
            "rejected": [dict(entry) for entry in self.rejected], "error": self.error,
            "prompt": dict(self.prompt) if self.prompt else None, "masked": dict(self.masked),
            "duration_ms": self.duration_ms,
        }


def _usable(roles: dict[str, str]) -> bool:
    """Okuyucunun (``ingest.tables.interpret``) tabloyu tanıma ölçütü."""
    present = set(roles.values())
    has_mapping = {"source_table", "target_table"} <= present
    has_jobs = "job" in present and bool(present & {"predecessor", "source_table", "target_table"})
    return has_mapping or has_jobs


def _label(role: str) -> str:
    return ROLE_LABELS.get(role, role)


def review_table_roles(reply: Any, headers: list[str], *,
                       sent_headers: list[str] | None = None) -> TableRoleSuggestion:
    """Ayrıştırılmış bir ``table-roles/1`` yanıtını başlıklara göre denetler.

    ``sent_headers`` LLM'e giden (maskeli) başlıklardır; model onları gördüğü için anahtarlar
    önce onlarla eşlenir, sonuç özgün başlıklarla döner.
    """
    result = TableRoleSuggestion()
    originals = [str(h if h is not None else "").strip() for h in headers]
    lookup: dict[str, str] = {}
    for index, original in enumerate(originals):
        if not original:
            continue
        sent = str(sent_headers[index]).strip() if sent_headers and index < len(sent_headers) else original
        lookup.setdefault(sent, original)
        lookup.setdefault(original, original)

    if not isinstance(reply, dict):
        result.reasons.append("yanıt bir JSON nesnesi değil")
        return result
    kind = reply.get("table_kind")
    kind = kind.strip().lower() if isinstance(kind, str) else kind
    if kind not in TABLE_KINDS:
        result.reasons.append(f"geçersiz tablo türü: {reply.get('table_kind')!r} "
                              f"(geçerliler: {', '.join(TABLE_KINDS)})")
        return result
    roles = reply.get("roles")
    if roles is None:
        roles = {}
    if not isinstance(roles, dict):
        result.reasons.append("'roles' başlık → rol sözlüğü olmalı")
        return result

    def reject(header: Any, role: Any, reason: str) -> None:
        result.rejected.append({"header": str(header), "role": role if isinstance(role, str) else str(role),
                                "reason": reason})

    valid: dict[str, str] = {}
    for key, role in roles.items():
        role_name = role.strip().lower() if isinstance(role, str) else role
        if role_name in (None, ""):
            continue  # model bu başlığı eşlemedi
        header = lookup.get(str(key).strip())
        if header is None:
            reject(key, role, f"'{key}' başlıklarda birebir geçmiyor")
        elif not isinstance(role_name, str) or role_name not in DEFAULT_ROLES:
            reject(key, role, f"'{role}' bilinen bir rol değil")
        elif header in valid:
            reject(key, role, f"'{header}' başlığına birden çok rol verilmiş")
        else:
            valid[header] = role_name

    owners: dict[str, list[str]] = {}
    for header, role in valid.items():
        owners.setdefault(role, []).append(header)
    for role, names in owners.items():
        if len(names) > 1:
            for header in names:
                del valid[header]
                reject(header, role, f"'{_label(role)}' rolü birden çok başlığa verilmiş ({', '.join(names)})")

    header_row = reply.get("header_row", 1)
    if isinstance(header_row, bool) or not isinstance(header_row, int) or header_row < 1:
        header_row = 1
    result.table_roles = TableRoles(
        table_kind=kind, header_row=header_row, roles=valid,
        unmapped=[h for h in dict.fromkeys(originals) if h and h not in valid],
        evidence=" | ".join(originals),
    )
    result.reasons.extend(dict.fromkeys(entry["reason"] for entry in result.rejected))
    result.usable = _usable(valid)
    if not result.usable:
        result.reasons.append(
            "eşleme kullanılabilir değil: kaynaktan hedefe eşleme için "
            f"'{_label('source_table')}' ve '{_label('target_table')}', job listesi için "
            f"'{_label('job')}' ile birlikte '{_label('predecessor')}' ya da bir tablo rolü gerekir")
    return result


def roles_to_synonyms(table_roles: TableRoles | dict[str, str] | None) -> dict[str, list[str]]:
    """Onaylanan eşlemeden ayarlara eklenecek eş anlamlılar: {rol: [başlık]}.

    Çıktı ayarların ``okuma.es_anlamlilar`` bölümüne olduğu gibi eklenebilir
    (``IngestConfig.from_settings`` başlıkları kendisi normalleştirir).
    """
    if table_roles is None:
        return {}
    roles = table_roles.roles if isinstance(table_roles, TableRoles) else dict(table_roles)
    found: dict[str, list[str]] = {}
    for header, role in roles.items():
        text = str(header).strip()
        if role in DEFAULT_ROLES and text and fold(text) and text not in found.get(role, []):
            found.setdefault(role, []).append(text)
    return {role: found[role] for role in DEFAULT_ROLES if role in found}


def _suggest(client: LLMClient, headers: list[Any], rows: list[list[Any]] | None, *, file: str, locator: str,
             prompt_version: int | None, user_prompt_dir: Path | None, mask: bool | None,
             dry_run: bool = False) -> TableRoleSuggestion:
    started = time.perf_counter()
    headers = [str(h if h is not None else "").strip() for h in headers]
    rows = [[str(c if c is not None else "").strip()[:MAX_CELL] for c in row] for row in (rows or [])[:MAX_ROWS]]
    masking = should_mask(client.config, mask)
    counts: dict[str, int] = {}

    def hide(value: str) -> str:
        if not masking:
            return value
        masked, found = mask_text(value)
        add_counts(counts, found)
        return masked

    sent_headers = [neutralize(hide(h)) for h in headers]
    sent_rows = [[neutralize(hide(c)) for c in row] for row in rows]
    try:
        prompt = prompt_library.load_prompt(PROMPT_ID, prompt_version, user_prompt_dir)
        sent = prompt_library.render(
            prompt, file=_attr(hide(str(file))), locator=_attr(hide(str(locator))),
            headers=" | ".join(sent_headers),
            rows="\n".join(" | ".join(row) for row in sent_rows) or "—",
        )
    except prompt_library.PromptError as exc:
        return TableRoleSuggestion(error=str(exc), reasons=[str(exc)], masked=counts)
    result = TableRoleSuggestion(prompt={"id": prompt["id"], "version": prompt["version"],
                                         "origin": prompt["origin"]}, sent=sent, masked=counts)
    if dry_run:
        return result
    raw = None
    try:
        raw = client.chat(prompt["system"], sent, [(s["user"], s["assistant"]) for s in prompt["fewshot"]])
        parsed = parse_json_reply(raw)
        reviewed = review_table_roles(parsed, headers, sent_headers=sent_headers)
    except LLMError as exc:
        result.raw, result.error = raw, str(exc)
        result.reasons = [f"LLM hatası: {str(exc)[:200]}"]
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        return result
    result.raw, result.parsed = raw, parsed
    result.table_roles, result.usable = reviewed.table_roles, reviewed.usable
    result.reasons, result.rejected = reviewed.reasons, reviewed.rejected
    result.duration_ms = int((time.perf_counter() - started) * 1000)
    return result


def suggest_table_roles(client: LLMClient, headers: list[Any], rows: list[list[Any]] | None, *, file: str,
                        locator: str, prompt_version: int | None = None, user_prompt_dir: Path | None = None,
                        mask: bool | None = None) -> TableRoleSuggestion:
    """Bir tablonun başlıkları (ve ilk ≤3 satırı) için rol önerisi ister ve denetler.

    Hata fırlatmaz: LLM ya da istem hatası ``error``/``reasons``'a yazılır. Satırlar
    kişisel veri içerebilir: ``mask=True`` ya da yerel olmayan adres maskeler.
    """
    return _suggest(client, headers, rows, file=file, locator=locator, prompt_version=prompt_version,
                    user_prompt_dir=user_prompt_dir, mask=mask)


def _unpack(entry: Any) -> tuple[str, str, list[Any], list[list[Any]]]:
    if isinstance(entry, dict):
        return (str(entry.get("file", "")), str(entry.get("locator", "")), list(entry.get("headers") or []),
                list(entry.get("rows") or []))
    file, locator, headers, rows = entry
    return str(file), str(locator), list(headers or []), list(rows or [])


def suggest_for_unrecognized(client: LLMClient, unrecognized_tables: list[Any], *,
                             prompt_version: int | None = None, user_prompt_dir: Path | None = None,
                             mask: bool | None = None, max_tables: int = MAX_TABLES) -> list[dict[str, Any]]:
    """``IngestResult.unrecognized_tables`` için onay kuyruğu öğeleri (JSON'a çevrilebilir).

    Her öğe: file, locator, headers, rows, suggestion (``table-roles/1`` sözlüğü ya da None),
    usable, synonyms (onaylanırsa eklenecek), reasons, rejected, error, prompt, reused.
    Aynı başlık dizisi bir kez sorulur (``reused``). ``max_tables`` farklı başlık dizisinden
    fazlası LLM'e gönderilmez; bunlar nedeniyle birlikte listelenir.
    """
    items: list[dict[str, Any]] = []
    cache: dict[tuple[str, ...], TableRoleSuggestion] = {}
    for entry in unrecognized_tables:
        file, locator, headers, rows = _unpack(entry)
        base = {"file": file, "locator": locator, "headers": [str(h) for h in headers],
                "rows": [[str(c) for c in row] for row in rows[:MAX_ROWS]]}
        key = tuple(str(h).strip() for h in headers)
        reused = key in cache
        if not reused and len(cache) >= max_tables:
            items.append({**base, "suggestion": None, "usable": False, "synonyms": {},
                          "reasons": [f"tablo sınırı ({max_tables}) aşıldı; LLM'e gönderilmedi"], "rejected": [],
                          "error": None, "prompt": None, "masked": {}, "duration_ms": 0, "reused": False})
            continue
        if not reused:
            cache[key] = suggest_table_roles(client, headers, rows, file=file, locator=locator,
                                             prompt_version=prompt_version, user_prompt_dir=user_prompt_dir,
                                             mask=mask)
        items.append({**base, **cache[key].as_dict(), "reused": reused})
    return items

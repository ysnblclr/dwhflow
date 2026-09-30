"""İstem (prompt) kütüphanesi (US-6.3).

İstemler ``<id>.v<N>.yaml`` adlı YAML dosyalarıdır:

* **yerleşik** sürümler uygulamayla gelir (``llm/prompts/``), değiştirilmez;
* **kullanıcı** sürümleri çağıranın verdiği bir klasörde durur (ör. çalışma alanındaki
  ``istemler/``). Kullanıcı sürümü hiçbir yerleşik sürümün yerine geçmez: her kayıt bir
  **sonraki numarayı** alır. Böylece her çıktı hangi istemden geldiğini taşır
  (``llm:<model>@<id>/v<N>``) ve eski sürüme dönmek her zaman mümkündür.

Bir istem dosyasının içeriği::

    id: extract_flows
    version: 3
    task: ...                # kısa açıklama
    system: |                # sistem metni (olduğu gibi gönderilir)
    user: |                  # kullanıcı şablonu: {file}, {locator}, {text} … yer tutucuları
    fewshot:                 # isteğe bağlı örnek (kullanıcı, asistan) çiftleri
      - user: ...
        assistant: ...       # geçerli bir JSON nesnesi olmalı
    author: ...              # kullanıcı sürümlerinde
    created: 2026-09-29T10:12:00+03:00

Şablondaki yer tutucular görev başına sabittir; bilinmeyen ya da biçimli yer tutucu
(``{0}``, ``{text!r}``, ``{a.b}``) kayıtta reddedilir ki ``format()`` çalışma anında çökmesin.
Metinde süslü parantez gerekiyorsa ``{{`` ve ``}}`` yazılır.
"""

from __future__ import annotations

import os
import re
import string
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .client import LLMError, parse_json_reply

BUILTIN_DIR = Path(__file__).resolve().parent / "prompts"
_FILE = re.compile(r"^(?P<id>[a-z][a-z0-9_]*)\.v(?P<version>[1-9][0-9]{0,5})\.ya?ml$")
_CONTENT_KEYS = {"system", "user", "fewshot", "task", "notes", "based_on"}
#: Kayıtta yok sayılan (sistemin kendisinin yazdığı) alanlar — arayüz yüklenen istemi geri gönderebilir.
_META_KEYS = {"id", "version", "origin", "author", "created", "path"}
MAX_TEXT = 60_000


class PromptError(ValueError):
    """İstem bulunamadı ya da geçersiz."""


@dataclass(frozen=True)
class TaskSpec:
    id: str
    title: str
    #: kullanıcı şablonunda bulunması zorunlu yer tutucular
    required: frozenset[str]
    #: kullanılabilecek bütün yer tutucular
    allowed: frozenset[str]


TASKS: dict[str, TaskSpec] = {
    "extract_flows": TaskSpec(
        "extract_flows", "Serbest metinden akış çıkarımı (DAF dwhflow.extract/2)",
        frozenset({"file", "locator", "text"}),
        frozenset({"file", "locator", "text", "doc_type", "known_names"}),
    ),
    "table_roles": TaskSpec(
        "table_roles", "Tanınmayan tablo başlığı eşleme (DAF dwhflow.table-roles/1)",
        frozenset({"headers"}),
        frozenset({"file", "locator", "headers", "rows"}),
    ),
}


def task_of(prompt_id: str) -> TaskSpec:
    try:
        return TASKS[prompt_id]
    except (KeyError, TypeError):
        known = ", ".join(TASKS)
        raise PromptError(f"bilinmeyen istem: {prompt_id!r} (bilinenler: {known})") from None


# --------------------------------------------------------------------------- #
# Şablon denetimi
# --------------------------------------------------------------------------- #


def placeholders(template: str) -> set[str]:
    """Şablondaki yer tutucu adları. Yalnız ``{ad}`` biçimi geçerlidir."""
    found: set[str] = set()
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError as exc:
        raise PromptError(f"şablon ayrıştırılamadı ({exc}); süslü parantez için {{{{ ve }}}} yazın") from exc
    for _, field, spec, conversion in parsed:
        if field is None:
            continue
        if not field.isidentifier():
            raise PromptError(f"geçersiz yer tutucu: {{{field}}} — yalnız {{ad}} biçimi kullanılabilir")
        if spec or conversion:
            raise PromptError(f"yer tutucuda biçim belirteci kullanılamaz: {{{field}}}")
        found.add(field)
    return found


def check_template(prompt_id: str, template: str) -> set[str]:
    """Kullanıcı şablonunu görevin yer tutucularına göre denetler; kullanılanları döner."""
    task = task_of(prompt_id)
    used = placeholders(template)
    unknown = sorted(used - task.allowed)
    if unknown:
        allowed = ", ".join(f"{{{name}}}" for name in sorted(task.allowed))
        raise PromptError(f"bilinmeyen yer tutucu: {', '.join('{' + n + '}' for n in unknown)} "
                          f"(kullanılabilenler: {allowed})")
    missing = sorted(task.required - used)
    if missing:
        raise PromptError("şablonda zorunlu yer tutucu eksik: "
                          + ", ".join("{" + name + "}" for name in missing))
    return used


def render(prompt: dict[str, Any], **values: Any) -> str:
    """İstemin kullanıcı şablonunu doldurur. Verilmeyen izinli yer tutucular boş kalır."""
    task = task_of(prompt["id"])
    check_template(prompt["id"], prompt["user"])
    return prompt["user"].format(**{name: str(values.get(name, "") or "") for name in task.allowed})


def validate_prompt(prompt_id: str, data: Any) -> dict[str, Any]:
    """İstem içeriğini denetler ve normalleştirilmiş içeriği döner (PromptError fırlatır)."""
    task_of(prompt_id)
    if not isinstance(data, dict):
        raise PromptError("istem bir sözlük olmalı (system, user, fewshot)")
    unknown = sorted(set(data) - _CONTENT_KEYS - _META_KEYS)
    if unknown:
        raise PromptError(f"bilinmeyen alan: {', '.join(unknown)} (geçerliler: system, user, fewshot, task, notes)")
    out: dict[str, Any] = {}
    for key in ("system", "user"):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise PromptError(f"'{key}' boş olmayan bir metin olmalı")
        if len(value) > MAX_TEXT:
            raise PromptError(f"'{key}' çok uzun (en çok {MAX_TEXT} karakter)")
        out[key] = value
    check_template(prompt_id, out["user"])
    shots = data.get("fewshot")
    if shots is None:
        shots = []
    if not isinstance(shots, list):
        raise PromptError("'fewshot' bir liste olmalı")
    out["fewshot"] = []
    for number, shot in enumerate(shots, start=1):
        if not isinstance(shot, dict) or set(shot) - {"user", "assistant"}:
            raise PromptError(f"{number}. örnek yalnız 'user' ve 'assistant' alanlarını içermeli")
        user, assistant = shot.get("user"), shot.get("assistant")
        if not (isinstance(user, str) and user.strip() and isinstance(assistant, str) and assistant.strip()):
            raise PromptError(f"{number}. örnekte 'user' ve 'assistant' boş olmayan metin olmalı")
        try:
            parse_json_reply(assistant)
        except LLMError as exc:
            raise PromptError(f"{number}. örneğin 'assistant' yanıtı geçerli JSON değil: {exc}") from exc
        out["fewshot"].append({"user": user, "assistant": assistant})
    for key in ("task", "notes"):
        value = data.get(key)
        if value is not None and not isinstance(value, str):
            raise PromptError(f"'{key}' bir metin olmalı")
        out[key] = value.strip() if isinstance(value, str) and value.strip() else None
    based_on = data.get("based_on")
    if based_on is not None and (isinstance(based_on, bool) or not isinstance(based_on, int) or based_on < 1):
        raise PromptError("'based_on' pozitif bir sürüm numarası olmalı")
    out["based_on"] = based_on
    return out


# --------------------------------------------------------------------------- #
# Dosyalar
# --------------------------------------------------------------------------- #


def _scan(directory: Path | None) -> dict[str, dict[int, Path]]:
    """Klasördeki istem dosyaları: {id: {sürüm: yol}}."""
    found: dict[str, dict[int, Path]] = {}
    if directory is None or not Path(directory).is_dir():
        return found
    for path in sorted(Path(directory).iterdir()):
        match = _FILE.match(path.name)
        if match and path.is_file():
            found.setdefault(match["id"], {}).setdefault(int(match["version"]), path)
    return found


def _versions(prompt_id: str, user_dir: Path | None) -> dict[int, tuple[str, Path]]:
    """{sürüm: (köken, yol)}. Yerleşik sürümle aynı numaralı kullanıcı dosyası yok sayılır."""
    out = {version: ("builtin", path) for version, path in _scan(BUILTIN_DIR).get(prompt_id, {}).items()}
    for version, path in _scan(user_dir).get(prompt_id, {}).items():
        out.setdefault(version, ("user", path))
    return dict(sorted(out.items()))


def _read(prompt_id: str, version: int, origin: str, path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise PromptError(f"{path.name} okunamadı: {exc}") from exc
    if not isinstance(data, dict):
        raise PromptError(f"{path.name}: istem dosyası bir sözlük olmalı")
    if data.get("id", prompt_id) != prompt_id:
        raise PromptError(f"{path.name}: içerikteki id ({data.get('id')!r}) dosya adıyla uyuşmuyor")
    if data.get("version", version) != version:
        raise PromptError(f"{path.name}: içerikteki sürüm ({data.get('version')!r}) dosya adıyla uyuşmuyor")
    try:
        content = validate_prompt(prompt_id, data)
    except PromptError as exc:
        raise PromptError(f"{path.name}: {exc}") from exc
    return {
        "id": prompt_id, "version": version, "task": content["task"] or task_of(prompt_id).title,
        "system": content["system"], "user": content["user"], "fewshot": content["fewshot"],
        "notes": content["notes"], "based_on": content["based_on"], "origin": origin,
        "author": data.get("author"), "created": _text(data.get("created")),
    }


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def load_prompt(prompt_id: str, version: int | None = None, user_dir: Path | None = None) -> dict[str, Any]:
    """İstemi yükler; ``version`` verilmezse en yeni sürüm (kullanıcı sürümleri dahil).

    Döner: id, version, task, system, user, fewshot [{user, assistant}], origin
    (builtin | user), author, created, notes, based_on.
    """
    task_of(prompt_id)
    versions = _versions(prompt_id, Path(user_dir) if user_dir else None)
    if not versions:
        raise PromptError(f"'{prompt_id}' için istem dosyası yok")
    if version is None:
        version = max(versions)
    if isinstance(version, str) and version.strip().isdigit():  # ör. API sorgu parametresi
        version = int(version)
    if isinstance(version, bool) or not isinstance(version, int) or version not in versions:
        raise PromptError(f"'{prompt_id}' isteminin v{version} sürümü yok "
                          f"(mevcut: {', '.join(f'v{v}' for v in versions)})")
    origin, path = versions[version]
    return _read(prompt_id, version, origin, path)


def list_prompts(user_dir: Path | None = None) -> list[dict[str, Any]]:
    """Görev başına istem sürümleri. Bozuk kullanıcı dosyaları listelenmez, ``errors``'a yazılır."""
    out = []
    for prompt_id, task in TASKS.items():
        versions, errors = [], []
        for version, (origin, path) in _versions(prompt_id, Path(user_dir) if user_dir else None).items():
            try:
                prompt = _read(prompt_id, version, origin, path)
            except PromptError as exc:
                errors.append(str(exc))
                continue
            versions.append({"version": version, "origin": origin, "task": prompt["task"],
                             "author": prompt["author"], "created": prompt["created"],
                             "notes": prompt["notes"], "based_on": prompt["based_on"],
                             "fewshot": len(prompt["fewshot"])})
        out.append({
            "id": prompt_id, "title": task.title,
            "task": versions[-1]["task"] if versions else task.title,
            "latest": versions[-1]["version"] if versions else None,
            "versions": versions, "errors": errors,
            "placeholders": {"required": sorted(task.required), "allowed": sorted(task.allowed)},
        })
    return out


class _Dumper(yaml.SafeDumper):
    pass


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.nodes.ScalarNode:
    # Çok satırlı metinler okunabilir blok biçiminde yazılır (mümkün değilse PyYAML tırnaklar).
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|" if "\n" in value else None)


_Dumper.add_representer(str, _represent_str)


def save_prompt_version(prompt_id: str, data: dict[str, Any], user_dir: Path,
                        author: str | None = None) -> int:
    """Yeni bir kullanıcı sürümü kaydeder ve numarasını döner.

    Numara, yerleşik ve kullanıcı sürümlerinin (bozuk dosyalar dahil) en büyüğünün bir
    fazlasıdır; mevcut hiçbir dosyanın üzerine yazılmaz. Dosya önce geçici adla yazılıp
    sonra yerine taşınır (yarım dosya kalmaz).
    """
    content = validate_prompt(prompt_id, data)
    if user_dir is None:
        raise PromptError("kullanıcı istem klasörü verilmedi")
    folder = Path(user_dir)
    folder.mkdir(parents=True, exist_ok=True)
    taken = set(_scan(BUILTIN_DIR).get(prompt_id, {})) | set(_scan(folder).get(prompt_id, {}))
    version = max(taken, default=0) + 1
    while any((folder / f"{prompt_id}.v{version}{suffix}").exists() for suffix in (".yaml", ".yml")):
        version += 1
    record: dict[str, Any] = {
        "id": prompt_id, "version": version,
        "task": content["task"] or task_of(prompt_id).title,
        "author": (str(author).strip()[:100] or None) if author else None,
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if content["based_on"] is not None:
        record["based_on"] = content["based_on"]
    if content["notes"]:
        record["notes"] = content["notes"]
    record.update({"system": content["system"], "user": content["user"], "fewshot": content["fewshot"]})
    text = yaml.dump(record, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=100_000)
    target = folder / f"{prompt_id}.v{version}.yaml"
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=folder, prefix=f".{prompt_id}.",
                                         suffix=".tmp", delete=False)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, target)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise
    return version

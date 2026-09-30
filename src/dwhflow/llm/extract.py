"""LLM ile serbest metinden DAF çıkarımı (``dwhflow.extract/2``) — kanıt denetimli.

Kuralların çözemediği paragraflar (tablo adı geçen ama akış yönü kalıptan anlaşılamayan)
buraya gelir. İşleyiş (PLAN_V2 §3.6, US-6.1 ve US-6.6):

1. **Parçalama** — paragraflar dosya başına, çağrı başına ``max_chars_per_call`` karakteri
   aşmayacak parçalarda toplanır. Uzun paragraf cümle sınırından bölünür; her parçanın
   konumu (başlık yolu dahil) korunur, ``<belge konum="…">`` ile modele de verilir.
2. **Maskeleme** — istenirse ya da LLM adresi yerel değilse kişisel veri maskelenir
   (``mask.py``). Model yalnız maskeli metni görür; denetim de maskeli metne göre yapılır.
3. **Ayrıştırma ve şema** — ``<think>``/kod çiti temizlenir, her öğe DAF şemasına
   (``daf.ExtractDoc`` parçaları) göre ayrı ayrı doğrulanır; geçersiz enum ``null`` olur.
4. **Dayanak denetimi** — model ne kadar ikna edici olursa olsun:

   * kanıt metinde harfi harfine geçmiyorsa (boşluk ve büyük/küçük harf farkı hariç),
   * kullanılan bir ad (tablo, akış kaynağı/hedefi, bağımlılık job'ları) metinde geçmiyorsa
     (şema öneki toleranslı: ``DWH.X`` ≈ ``X``),
   * kaynak ile hedef aynıysa

   öğe **reddedilir** ve nedeni sayılır. Uydurma süreç adı akışı düşürmez, yalnız süreci
   atar; metinde geçmeyen kolon ya da özellik (araç, sistem, saat, dönüşüm ifadesi) yalnız
   o alanı düşürür. Tekrar eden öğeler birleşir.
5. **Uygulama** — kabul edilenler ``daf.apply_extract`` ile **taslak** girer; kaynak
   ``llm:<model>@<istem>/v<sürüm>``, güven en çok 0.8. ``unresolved`` cümleler onay kuyruğu
   için istatistikte (dosya ve konumla) döner.

v1 biçimli yanıtlar (``{"flows": [{"source", "target", "job", "evidence", "confidence"}]}``)
de kabul edilir (``job`` → ``process``).
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from ..daf import (
    DafDataset,
    DafDependency,
    DafFlow,
    DafProcess,
    DafSchedule,
    DafUnresolved,
    ExtractDoc,
    apply_extract,
)
from ..ingest.base import DocReport, ModelBuilder, bare_key, is_file_name, name_key
from . import prompts as prompt_library
from .client import LLMClient, LLMError, parse_json_reply
from .mask import add_counts, describe_counts, mask_text, should_mask

DEFAULT_PROMPT = "extract_flows"
#: En yeni yerleşik akış çıkarım istemi (geriye uyumluluk için dosya yolu olarak da tutulur).
PROMPT_FILE = prompt_library.BUILTIN_DIR / "extract_flows.v2.yaml"
MAX_CONFIDENCE = 0.8  # LLM çıkarımı hiçbir zaman kesin sayılmaz
MIN_CONFIDENCE = 0.1
#: Çok küçük parça sınırı anlamsız sayıda çağrı üretir; altına inilmez.
MIN_CHUNK_CHARS = 200
#: Bir çağrıda modele verilecek en çok bilinen ad.
MAX_KNOWN_NAMES = 60
#: Tablo/süreç adı için üst sınır (cümleyi ad diye döndüren modele karşı).
MAX_NAME_CHARS = 128
#: Zamanlama saati: modelde ``HH:MM`` tutulur (doğrulama kuralları saatle hesap yapar).
_TIME = re.compile(r"^([01]?\d|2[0-3])[:.]([0-5]\d)$")

#: Red nedenleri (kod → açıklama). ``LLMStats.reasons`` bu kodlarla sayar.
REJECT_REASONS: dict[str, str] = {
    "sema": "şemaya uymuyor",
    "kanit": "kanıt metinde birebir geçmiyor",
    "ad": "ad metinde geçmiyor",
    "dongu": "kaynak ile hedef aynı",
    "belirsiz": "belirsiz cümle metinde geçmiyor",
}
#: Öğe kabul edildi ama bir parçası atıldı (kod → açıklama). ``LLMStats.dropped``.
DROP_REASONS: dict[str, str] = {
    "surec": "süreç adı metinde geçmiyor; akış süreçsiz kabul edildi",
    "kolon": "kolon adı metinde geçmiyor; kolon atıldı",
    "ozellik": "özellik metinde geçmiyor; alan boşaltıldı",
}
_PLURAL = {"dataset": "datasets", "process": "processes", "flow": "flows", "dependency": "dependencies"}
_REPLY_KEYS = {"schema", "datasets", "processes", "flows", "dependencies", "unresolved"}


# --------------------------------------------------------------------------- #
# Geriye uyumluluk
# --------------------------------------------------------------------------- #


def load_prompt(path: Path | None = None) -> dict[str, Any]:
    """Yol verilmezse ``extract_flows`` isteminin en yeni yerleşik sürümü; verilirse o YAML dosyası.

    Sürüm seçimi ve kullanıcı sürümleri için ``prompts.load_prompt`` kullanın.
    """
    if path is None:
        return prompt_library.load_prompt(DEFAULT_PROMPT)
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Metin normalizasyonu ve dayanak denetimi
# --------------------------------------------------------------------------- #

_QUOTES = str.maketrans({"’": "'", "‘": "'", "‛": "'", "“": '"', "”": '"', "„": '"'})
_NAME_NOISE = re.compile(r'[\[\]"`]')


def _norm(text: Any) -> str:
    """Karşılaştırma biçimi: Unicode NFKC, tipografik tırnak düz, büyük/küçük harf ve
    Türkçe noktalı/noktasız i farkı yok, tek boşluk."""
    value = unicodedata.normalize("NFKC", str(text)).translate(_QUOTES)
    value = value.casefold().replace("̇", "").replace("ı", "i")
    return re.sub(r"\s+", " ", value).strip()


def _name_norm(text: Any) -> str:
    """Ad karşılaştırma biçimi: ``_norm`` + köşeli parantez/tırnak yok (``[DWH].[X]`` ≈ ``DWH.X``)."""
    return _norm(_NAME_NOISE.sub("", str(text)))


def _key(name: Any) -> str:
    """Tekilleştirme anahtarı: büyük harf, şema öneki yok."""
    return bare_key(name_key(str(name)))


class Grounding:
    """Bir parçanın (modelin gördüğü) metnine karşı dayanak denetimi."""

    def __init__(self, text: str) -> None:
        self.text = text
        self._plain = _norm(text)
        self._names = _name_norm(text)

    def phrase(self, value: Any) -> bool:
        """Metin parçası (kanıt, dönüşüm ifadesi, saat …) metinde birebir geçiyor mu?"""
        needle = _norm(value) if value is not None else ""
        return bool(needle) and needle in self._plain

    def name(self, value: Any) -> bool:
        """Ad metinde **tam sözcük** olarak geçiyor mu? ``DWH.X`` metinde ``X`` olarak geçebilir,
        ``X`` de metinde ``DWH.X`` olarak; ama ``KART`` ``STG_KART_ISLEM`` içinde geçmiş sayılmaz."""
        needle = _name_norm(value) if value is not None else ""
        if not needle:
            return False
        candidates = [needle]
        if not is_file_name(needle):
            parts = needle.split(".")
            candidates += [".".join(parts[i:]) for i in range(1, len(parts))]
        return any(
            re.search(rf"(?<!\w){re.escape(candidate)}(?!\w)", self._names)
            for candidate in candidates if candidate
        )


def _fold_map(text: str) -> tuple[str, list[int]]:
    """``_norm`` ile aynı karşılaştırma biçimi, karakter karakter; her çıktı karakterinin özgün
    metindeki yeri de döner (kanıt aralığını bulmak için). Metin önceden NFKC olmalıdır."""
    chars: list[str] = []
    index: list[int] = []
    space_at: int | None = None
    for position, char in enumerate(text):
        if char.isspace():
            if space_at is None:
                space_at = position
            continue
        folded = unicodedata.normalize("NFKC", char).translate(_QUOTES).casefold()
        folded = folded.replace("̇", "").replace("ı", "i")
        if not folded:
            continue
        if space_at is not None and chars:
            chars.append(" ")
            index.append(space_at)
        space_at = None
        for piece in folded:
            chars.append(piece)
            index.append(position)
    return "".join(chars), index


def find_span(text: str, evidence: str) -> tuple[int, int] | None:
    """Kanıtın metindeki karakter aralığı (vurgulama için); dayanak denetimiyle aynı toleranslar."""
    needle = _norm(evidence or "")
    if not needle:
        return None
    folded, index = _fold_map(text)
    start = folded.find(needle)
    if start < 0:
        return None
    return index[start], index[start + len(needle) - 1] + 1


# --------------------------------------------------------------------------- #
# Parçalama
# --------------------------------------------------------------------------- #

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
# Zarf etiketlerini metin içinden kapatma/açma girişimleri (istem enjeksiyonu) etkisiz kılınır.
_ENVELOPE_TAG = re.compile(r"<(\s*/?\s*(?:belge|bilinen_adlar|tablo|basliklar|ornek_satirlar)\b)", re.IGNORECASE)


def neutralize(text: str) -> str:
    """Metindeki ``<belge>``, ``</belge>`` … etiketlerini ``‹belge>`` yapar: zarf metinden kırılamaz."""
    return _ENVELOPE_TAG.sub(r"‹\1", text)


def _attr(value: Any) -> str:
    """Zarf özniteliği (dosya, konum, tür) için güvenli metin."""
    return re.sub(r"\s+", " ", neutralize(str(value or ""))).replace('"', "'").strip()


def split_text(text: str, limit: int) -> list[str]:
    """Metni cümle sınırından ``limit`` karakteri aşmayan parçalara böler (tek cümle aşarsa boşluktan)."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    pieces: list[str] = []
    buffer = ""
    for sentence in _SENTENCE_END.split(text):
        sentence = sentence.strip()
        while len(sentence) > limit:
            cut = sentence.rfind(" ", 0, limit + 1)
            if cut <= 0:
                cut = limit
            head, sentence = sentence[:cut].rstrip(), sentence[cut:].lstrip()
            if buffer:
                pieces.append(buffer)
                buffer = ""
            pieces.append(head)
        if not sentence:
            continue
        if buffer and len(buffer) + 1 + len(sentence) > limit:
            pieces.append(buffer)
            buffer = sentence
        else:
            buffer = f"{buffer} {sentence}" if buffer else sentence
    if buffer:
        pieces.append(buffer)
    return pieces


@dataclass
class Segment:
    """Parça metni içinde bir paragrafın yeri."""

    locator: str
    start: int
    end: int


@dataclass
class Chunk:
    """LLM'e tek çağrıda giden metin (maskelenmiş, zarf etiketleri etkisiz)."""

    file: str
    locator: str
    text: str
    segments: list[Segment] = field(default_factory=list)
    masked: dict[str, int] = field(default_factory=dict)
    #: Zarfa yazılan dosya adı ve konum (maskeleme açıksa maskeli)
    envelope_file: str = ""
    envelope_locator: str = ""

    def locate(self, evidence: str) -> tuple[str, tuple[int, int] | None]:
        """Kanıtın geçtiği paragrafın konumu ve metindeki aralığı."""
        span = find_span(self.text, evidence)
        if span is not None:
            for segment in self.segments:
                if segment.start <= span[0] and span[1] <= segment.end:
                    return segment.locator, span
            return self.locator, span
        needle = _norm(evidence)
        for segment in self.segments:
            if needle and needle in _norm(self.text[segment.start:segment.end]):
                return segment.locator, None
        return self.locator, None


def _join_locators(locators: list[str]) -> str:
    unique = list(dict.fromkeys(locators))
    return " / ".join(unique[:3]) + (" / …" if len(unique) > 3 else "")


def _envelope(chunk: Chunk, mask: bool) -> Chunk:
    file, locator = chunk.file, chunk.locator
    if mask:
        file, _ = mask_text(file)
        locator, _ = mask_text(locator)
    chunk.envelope_file, chunk.envelope_locator = _attr(file), _attr(locator)
    return chunk


def make_chunks(file: str, paragraphs: Iterable[tuple[str, str]], limit: int | None, *,
                mask: bool = False) -> list[Chunk]:
    """Bir dosyanın paragraflarını LLM çağrılarına böler.

    ``limit`` ``None`` ise bölme yapılmaz (tek parça; test konsolu). Paragraf önce maskelenir,
    sonra bölünür: maskeleme uzunluğu değiştirir.
    """
    size_limit = None if limit is None else max(MIN_CHUNK_CHARS, int(limit))
    pieces: list[tuple[str, str, dict[str, int]]] = []
    for locator, text in paragraphs:
        # NFKC: PDF'lerdeki ayrık aksanlar, bitişik harfler (ﬁ), tam genişlikli rakamlar tek biçime iner;
        # model de denetim de aynı metni görür.
        value = unicodedata.normalize("NFKC", str(text or ""))
        counts: dict[str, int] = {}
        if mask:
            value, counts = mask_text(value)
        value = neutralize(value).strip()
        parts = [value] if size_limit is None else split_text(value, size_limit)
        parts = [part for part in parts if part]
        for number, part in enumerate(parts, start=1):
            where = f"{locator} · parça {number}/{len(parts)}" if len(parts) > 1 else str(locator)
            pieces.append((where, part, counts if number == 1 else {}))

    chunks: list[Chunk] = []
    buffer: list[tuple[str, str, dict[str, int]]] = []

    def flush() -> None:
        text, segments, masked = "", [], {}
        for where, part, counts in buffer:
            if text:
                text += "\n\n"
            segments.append(Segment(where, len(text), len(text) + len(part)))
            text += part
            add_counts(masked, counts)
        locator = _join_locators([segment.locator for segment in segments])
        chunks.append(_envelope(Chunk(file, locator, text, segments, masked), mask))
        buffer.clear()

    size = 0
    for piece in pieces:
        if buffer and size_limit is not None and size + 2 + len(piece[1]) > size_limit:
            flush()
            size = 0
        size += len(piece[1]) + (2 if buffer else 0)
        buffer.append(piece)
    if buffer:
        flush()
    return chunks


def relevant_names(known: Iterable[str], text: str, limit: int = MAX_KNOWN_NAMES) -> list[str]:
    """Bilinen adlardan bu metinde (tam sözcük, şema öneki toleranslı) geçenler — yazım hizalaması için."""
    ground = Grounding(text)
    out: list[str] = []
    seen: set[str] = set()
    for raw in known:
        name = str(raw or "").strip()
        if not name or name.upper() in seen:
            continue
        if ground.name(name):
            out.append(name)
            seen.add(name.upper())
            if len(out) >= limit:
                break
    return out


# --------------------------------------------------------------------------- #
# Yanıt denetimi
# --------------------------------------------------------------------------- #

_SCHEMA_MESSAGES = {
    "missing": "zorunlu alan eksik", "string_type": "metin olmalı", "list_type": "liste olmalı",
    "model_type": "nesne olmalı", "dict_type": "nesne olmalı", "string_too_short": "boş olamaz",
    "bool_type": "true/false olmalı", "bool_parsing": "true/false olmalı",
}


def _schema_message(exc: ValidationError) -> str:
    error = exc.errors()[0]
    where = ".".join(str(part) for part in error.get("loc", ())) or "öğe"
    return f"şemaya uymuyor ({where}: {_SCHEMA_MESSAGES.get(error.get('type', ''), error.get('msg', ''))})"


@dataclass
class AcceptedItem:
    """Denetimden geçen bir DAF öğesi."""

    type: str  # dataset | process | flow | dependency
    item: Any  # daf.DafDataset | DafProcess | DafFlow | DafDependency
    key: tuple[Any, ...]
    locator: str
    span: tuple[int, int] | None = None
    dropped: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"type": self.type, "item": self.item.model_dump(mode="json"), "locator": self.locator,
                "span": list(self.span) if self.span else None, "dropped": list(self.dropped)}


@dataclass
class Review:
    """Bir LLM yanıtının denetim sonucu."""

    accepted: list[AcceptedItem] = field(default_factory=list)
    #: {"type", "item" (ham öğe), "code", "reason"}
    rejected: list[dict[str, Any]] = field(default_factory=list)
    #: {"type", "name", "code", "reason"} — öğe kabul edildi, bir parçası atıldı
    dropped: list[dict[str, Any]] = field(default_factory=list)
    #: {"text", "reason", "locator"}
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    #: aynı yanıtta tekrar eden (birleştirilen) öğe sayısı
    merged: int = 0

    def reject(self, kind: str, item: Any, code: str, reason: str) -> None:
        self.rejected.append({"type": kind, "item": item, "code": code, "reason": reason})


def extract_doc(items: Iterable[AcceptedItem]) -> ExtractDoc:
    """Kabul edilen öğelerden ``dwhflow.extract/2`` belgesi."""
    items = list(items)

    def of(kind: str) -> list[Any]:
        return [entry.item for entry in items if entry.type == kind]

    return ExtractDoc(datasets=of("dataset"), processes=of("process"), flows=of("flow"),
                      dependencies=of("dependency"))


def _unique(columns: list[Any], key: Callable[[Any], Any]) -> list[Any]:
    seen: set[Any] = set()
    out = []
    for column in columns:
        if key(column) not in seen:
            seen.add(key(column))
            out.append(column)
    return out


def _merge(kind: str, old: Any, new: Any) -> Any:
    if kind == "dataset":
        return old.model_copy(update={
            "columns": _unique(old.columns + new.columns, lambda c: c.name.upper()),
            "kind": old.kind or new.kind, "layer": old.layer or new.layer, "system": old.system or new.system,
        })
    if kind == "process":
        return old.model_copy(update={
            "kind": old.kind or new.kind, "tool": old.tool or new.tool,
            "load_strategy": old.load_strategy or new.load_strategy, "schedule": old.schedule or new.schedule,
        })
    if kind == "flow":
        return old.model_copy(update={
            "columns": _unique(old.columns + new.columns, lambda c: (c.source.upper(), c.target.upper())),
            "confidence": max(old.confidence, new.confidence),
        })
    return old


class _Reviewer:
    def __init__(self, chunk: Chunk, cap: float) -> None:
        self.chunk = chunk
        self.cap = cap
        self.ground = Grounding(chunk.text)
        self.review = Review()
        self.index: dict[tuple[Any, ...], AcceptedItem] = {}
        self.unresolved_keys: set[str] = set()

    def run(self, reply: Any) -> Review:
        if not isinstance(reply, dict) or not (_REPLY_KEYS & set(reply)):
            self.review.reject("yanit", reply, "sema",
                               "yanıtta beklenen alanlar yok (datasets, processes, flows, dependencies, unresolved)")
            return self.review
        handlers = (("datasets", "dataset", self.dataset), ("processes", "process", self.process),
                    ("flows", "flow", self.flow), ("dependencies", "dependency", self.dependency),
                    ("unresolved", "unresolved", self.unresolved))
        for key, kind, handler in handlers:
            values = reply.get(key)
            if values is None:
                continue
            if not isinstance(values, list):
                self.review.reject(kind, values, "sema", f"'{key}' bir liste değil")
                continue
            for raw in values:
                handler(raw)
        return self.review

    # -- yardımcılar -------------------------------------------------------- #

    def _parse(self, model: type[BaseModel], raw: Any, kind: str) -> Any:
        try:
            return model.model_validate(raw)
        except ValidationError as exc:
            self.review.reject(kind, raw, "sema", _schema_message(exc))
            return None

    def _check_names(self, kind: str, raw: Any, *names: str) -> bool:
        for name in names:
            if not _key(name) or len(name) > MAX_NAME_CHARS or not re.search(r"[^\W_]", name):
                self.review.reject(kind, raw, "ad", f"'{name[:80]}' geçerli bir ad değil")
                return False
            if not self.ground.name(name):
                self.review.reject(kind, raw, "ad", f"'{name}' metinde geçmiyor")
                return False
        return True

    def _drop(self, kind: str, name: str, code: str, detail: str, dropped: list[str]) -> None:
        self.review.dropped.append({"type": kind, "name": name, "code": code, "reason": detail})
        dropped.append(detail)

    def _accept(self, kind: str, item: Any, key: tuple[Any, ...], dropped: list[str]) -> None:
        existing = self.index.get(key)
        if existing is not None:
            existing.item = _merge(kind, existing.item, item)
            existing.dropped.extend(d for d in dropped if d not in existing.dropped)
            self.review.merged += 1
            return
        locator, span = self.chunk.locate(item.evidence)
        if span is not None:  # kanıt olarak belgenin kendi metni saklanır
            item = item.model_copy(update={"evidence": self.chunk.text[span[0]:span[1]]})
        accepted = AcceptedItem(kind, item, key, locator, span, dropped)
        self.index[key] = accepted
        self.review.accepted.append(accepted)

    # -- öğe türleri -------------------------------------------------------- #

    def dataset(self, raw: Any) -> None:
        item = self._parse(DafDataset, raw, "dataset")
        if item is None:
            return
        if not self.ground.phrase(item.evidence):
            return self.review.reject("dataset", raw, "kanit", REJECT_REASONS["kanit"])
        if not self._check_names("dataset", raw, item.name):
            return
        dropped: list[str] = []
        columns = []
        for column in _unique(item.columns, lambda c: c.name.upper()):
            if self.ground.name(column.name):
                columns.append(column)
            else:
                self._drop("dataset", item.name, "kolon", f"'{column.name}' kolonu metinde geçmiyor", dropped)
        system = item.system
        if system and not self.ground.phrase(system):
            self._drop("dataset", item.name, "ozellik", f"sistem '{system}' metinde geçmiyor", dropped)
            system = None
        item = item.model_copy(update={"columns": columns, "system": system or None})
        self._accept("dataset", item, ("dataset", _key(item.name)), dropped)

    def process(self, raw: Any) -> None:
        item = self._parse(DafProcess, raw, "process")
        if item is None:
            return
        if not self.ground.phrase(item.evidence):
            return self.review.reject("process", raw, "kanit", REJECT_REASONS["kanit"])
        if not self._check_names("process", raw, item.name):
            return
        dropped: list[str] = []
        tool = item.tool
        if tool and not self.ground.phrase(tool):
            self._drop("process", item.name, "ozellik", f"araç '{tool}' metinde geçmiyor", dropped)
            tool = None
        schedule = item.schedule
        if schedule is not None:
            times: list[str] = []
            for moment in schedule.times:
                match = _TIME.match(moment.strip())
                if match is None:
                    self._drop("process", item.name, "ozellik", f"saat '{moment}' SS:DD biçiminde değil", dropped)
                elif not self.ground.phrase(moment):
                    self._drop("process", item.name, "ozellik", f"saat '{moment}' metinde geçmiyor", dropped)
                elif (value := f"{int(match[1]):02d}:{match[2]}") not in times:
                    times.append(value)
            calendar = schedule.calendar
            if calendar and not self.ground.phrase(calendar):
                self._drop("process", item.name, "ozellik", f"takvim '{calendar}' metinde geçmiyor", dropped)
                calendar = None
            schedule = DafSchedule(times=times, calendar=calendar) if (times or calendar) else None
        item = item.model_copy(update={"tool": tool or None, "schedule": schedule})
        self._accept("process", item, ("process", _key(item.name)), dropped)

    def flow(self, raw: Any) -> None:
        if isinstance(raw, dict) and "process" not in raw and "job" in raw:
            raw = {**raw, "process": raw.get("job")}  # v1 yanıt biçimi
        item = self._parse(DafFlow, raw, "flow")
        if item is None:
            return
        source, target = _key(item.source), _key(item.target)
        if source and source == target:
            return self.review.reject("flow", raw, "dongu", REJECT_REASONS["dongu"])
        if not self.ground.phrase(item.evidence):
            return self.review.reject("flow", raw, "kanit", REJECT_REASONS["kanit"])
        if not self._check_names("flow", raw, item.source, item.target):
            return
        dropped: list[str] = []
        label = f"{item.source} → {item.target}"
        process = item.process or None
        if process and (not self.ground.name(process) or _key(process) in (source, target, "")):
            self._drop("flow", label, "surec", f"süreç '{process}' metinde geçmiyor ya da geçerli değil", dropped)
            process = None
        columns = []
        for column in _unique(item.columns, lambda c: (c.source.upper(), c.target.upper())):
            if not (self.ground.name(column.source) and self.ground.name(column.target)):
                self._drop("flow", label, "kolon",
                           f"'{column.source}' → '{column.target}' kolon eşlemesi metinde geçmiyor", dropped)
                continue
            if column.expression and not self.ground.phrase(column.expression):
                self._drop("flow", label, "ozellik", f"dönüşüm ifadesi '{column.expression}' metinde geçmiyor",
                           dropped)
                column = column.model_copy(update={"expression": None})
            columns.append(column)
        confidence = min(self.cap, max(MIN_CONFIDENCE, item.confidence))
        item = item.model_copy(update={"process": process, "columns": columns, "confidence": confidence})
        self._accept("flow", item, ("flow", source, target, _key(process) if process else None), dropped)

    def dependency(self, raw: Any) -> None:
        item = self._parse(DafDependency, raw, "dependency")
        if item is None:
            return
        before, after = _key(item.before), _key(item.after)
        if before and before == after:
            return self.review.reject("dependency", raw, "dongu", "önceki ve sonraki job aynı")
        if not self.ground.phrase(item.evidence):
            return self.review.reject("dependency", raw, "kanit", REJECT_REASONS["kanit"])
        if not self._check_names("dependency", raw, item.before, item.after):
            return
        self._accept("dependency", item, ("dependency", before, after), [])

    def unresolved(self, raw: Any) -> None:
        item = self._parse(DafUnresolved, raw, "unresolved")
        if item is None:
            return
        if not self.ground.phrase(item.text):
            return self.review.reject("unresolved", raw, "belirsiz", REJECT_REASONS["belirsiz"])
        key = _norm(item.text)
        if key in self.unresolved_keys:
            self.review.merged += 1
            return
        self.unresolved_keys.add(key)
        locator, span = self.chunk.locate(item.text)
        text = self.chunk.text[span[0]:span[1]] if span else item.text
        self.review.unresolved.append({"text": text, "reason": (item.reason or "")[:300], "locator": locator})


def review_reply(reply: Any, chunk: Chunk, *, confidence_cap: float = MAX_CONFIDENCE) -> Review:
    """Ayrıştırılmış bir LLM yanıtını şema ve dayanak kurallarına göre denetler (modele dokunmaz)."""
    return _Reviewer(chunk, _cap(confidence_cap)).run(reply)


def _cap(confidence_cap: float) -> float:
    try:
        value = float(confidence_cap)
    except (TypeError, ValueError):
        value = MAX_CONFIDENCE
    return max(MIN_CONFIDENCE, min(MAX_CONFIDENCE, value))


# --------------------------------------------------------------------------- #
# Çağrı
# --------------------------------------------------------------------------- #


@dataclass
class ChunkResult:
    """Bir parçanın LLM çağrısı ve denetim sonucu."""

    chunk: Chunk
    sent: str = ""
    raw: str | None = None
    parsed: dict[str, Any] | None = None
    error: str | None = None
    review: Review | None = None
    duration_ms: int = 0


def build_user_message(prompt: dict[str, Any], chunk: Chunk, *, known_names: Iterable[str] = (),
                       doc_type: str = "belge") -> str:
    """İstem şablonunu parçayla doldurur (LLM'e giden kullanıcı mesajı)."""
    return prompt_library.render(
        prompt, file=chunk.envelope_file, locator=chunk.envelope_locator, text=chunk.text,
        doc_type=_attr(doc_type), known_names=neutralize(", ".join(known_names)),
    )


def ask_chunk(client: LLMClient, prompt: dict[str, Any], chunk: Chunk, *, known_names: Iterable[str] = (),
              doc_type: str = "belge", confidence_cap: float = MAX_CONFIDENCE) -> ChunkResult:
    """Parçayı LLM'e gönderir, yanıtı ayrıştırıp denetler. Hata fırlatmaz; ``error`` doldurur."""
    result = ChunkResult(chunk=chunk)
    started = time.perf_counter()
    try:
        result.sent = build_user_message(prompt, chunk, known_names=known_names, doc_type=doc_type)
        examples = [(shot["user"], shot["assistant"]) for shot in prompt.get("fewshot", [])]
        result.raw = client.chat(prompt["system"], result.sent, examples)
        result.parsed = parse_json_reply(result.raw)
        result.review = review_reply(result.parsed, chunk, confidence_cap=confidence_cap)
    except LLMError as exc:
        result.error = str(exc)
    except Exception as exc:  # noqa: BLE001 - beklenmeyen yanıt yalnız bu parçayı düşürsün, okumayı değil
        result.error = f"LLM yanıtı işlenemedi ({type(exc).__name__}): {exc}"
    finally:
        result.duration_ms = int((time.perf_counter() - started) * 1000)
    return result


# --------------------------------------------------------------------------- #
# İstatistik
# --------------------------------------------------------------------------- #


def _file_counts() -> dict[str, int]:
    return {"chunks": 0, "calls": 0, "accepted": 0, "rejected": 0, "unresolved": 0, "merged": 0,
            "errors": 0, "masked": 0, "columns_not_applied": 0}


@dataclass
class LLMStats:
    calls: int = 0
    accepted: int = 0
    rejected: int = 0
    errors: list[str] = field(default_factory=list)
    #: red kodu → adet (bkz. ``REJECT_REASONS``)
    reasons: dict[str, int] = field(default_factory=dict)
    #: atılan parça kodu → adet (bkz. ``DROP_REASONS``)
    dropped: dict[str, int] = field(default_factory=dict)
    #: tekrar eden (birleştirilen) öğe sayısı
    merged: int = 0
    #: kabul edilen öğe sayısı, türe göre
    items: dict[str, int] = field(default_factory=lambda: dict.fromkeys(
        ("datasets", "processes", "flows", "columns", "dependencies"), 0))
    #: onay kuyruğu için: {"file", "locator", "text", "reason", "extractor"}
    unresolved: list[dict[str, str]] = field(default_factory=list)
    #: dosya → sayılar (``_file_counts``)
    per_file: dict[str, dict[str, int]] = field(default_factory=dict)
    #: maskelenen kişisel veri, kurala göre
    masked: dict[str, int] = field(default_factory=dict)
    masking: bool = False
    chunks: int = 0
    cancelled: bool = False
    #: Denetimden geçen (``items["columns"]``'ta sayılan) ama model kurucu kolon kenarını taslak
    #: işaretleyemediği için modele eklenmeyen kolon eşlemeleri (bkz. ``daf.supports_draft_columns``).
    columns_not_applied: int = 0
    #: {"id", "version", "origin", "extractor"}
    prompt: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls, "accepted": self.accepted, "rejected": self.rejected,
            "errors": self.errors[:10], "reasons": dict(self.reasons), "dropped": dict(self.dropped),
            "merged": self.merged, "items": dict(self.items),
            "unresolved": [dict(entry) for entry in self.unresolved],
            "per_file": {file: dict(counts) for file, counts in self.per_file.items()},
            "masked": dict(self.masked), "masking": self.masking, "chunks": self.chunks,
            "cancelled": self.cancelled, "prompt": dict(self.prompt),
            "columns_not_applied": self.columns_not_applied,
        }


def _bump(counter: dict[str, int], key: str, amount: int = 1) -> None:
    counter[key] = counter.get(key, 0) + amount


# --------------------------------------------------------------------------- #
# v1 uyumluluğu: tek akış denetimi
# --------------------------------------------------------------------------- #


def validate_flow(item: Any, chunk: str) -> tuple[dict[str, Any] | None, str | None]:
    """Tek bir v1 akışını denetler. (kabul edilen, red nedeni)."""
    if not isinstance(item, dict):
        return None, "akış nesne değil"
    source = str(item.get("source") or "").strip()
    target = str(item.get("target") or "").strip()
    evidence = str(item.get("evidence") or "").strip()
    if not source or not target:
        return None, "kaynak ya da hedef boş"
    if source.casefold() == target.casefold() or _key(source) == _key(target):
        return None, "kaynak ile hedef aynı"
    ground = Grounding(chunk)
    if not evidence or not ground.phrase(evidence):
        return None, "kanıt cümlesi metinde birebir geçmiyor"
    for name in (source, target):
        if not ground.name(name):
            return None, f"'{name}' metinde geçmiyor"
    job = str(item.get("job") or "").strip() or None
    if job and not ground.name(job):
        job = None  # uydurma job adı akışı düşürmez, yalnız job'ı atar
    try:
        confidence = float(item.get("confidence", 0.6))
    except (TypeError, ValueError):
        confidence = 0.6
    return {
        "source": source, "target": target, "job": job, "evidence": evidence,
        "confidence": max(MIN_CONFIDENCE, min(MAX_CONFIDENCE, confidence)),
    }, None


# --------------------------------------------------------------------------- #
# Toplu çıkarım
# --------------------------------------------------------------------------- #


def _is_cancelled(cancel: Any) -> bool:
    if cancel is None:
        return False
    if hasattr(cancel, "is_set"):
        return bool(cancel.is_set())
    return bool(cancel())


def _run_all(chunks: list[Chunk], run: Callable[[Chunk], ChunkResult | None],
             workers: int) -> Iterator[tuple[Chunk, ChunkResult | None]]:
    """Parçaları sırayla ya da paralel çalıştırır; sonuçlar **her zaman sırayla** döner."""
    if workers <= 1 or len(chunks) < 2:
        for chunk in chunks:
            yield chunk, run(chunk)
        return
    with ThreadPoolExecutor(max_workers=min(workers, len(chunks)), thread_name_prefix="dwhflow-llm") as pool:
        yield from zip(chunks, pool.map(run, chunks), strict=True)


def _builder_names(builder: ModelBuilder) -> list[str]:
    names = [node.display for node in getattr(builder, "datasets", {}).values()]
    return names + [node.display for node in getattr(builder, "processes", {}).values()]


def _doc_type(report: DocReport | None) -> str:
    if report is None:
        return "belge"
    return str(report.format or report.reader or "belge").lower()


def _group_by_locator(items: list[AcceptedItem]) -> list[tuple[str, list[AcceptedItem]]]:
    groups: dict[str, list[AcceptedItem]] = {}
    for item in items:
        groups.setdefault(item.locator, []).append(item)
    return list(groups.items())


def extract_with_llm(
    candidates: list[tuple[str, str, str]],
    builder: ModelBuilder,
    reports: dict[str, DocReport],
    client: LLMClient,
    *,
    prompt_id: str = DEFAULT_PROMPT,
    version: int | None = None,
    confidence_cap: float = MAX_CONFIDENCE,
    known_names: Iterable[str] | None = None,
    mask: bool | None = None,
    user_prompt_dir: Path | None = None,
    workers: int = 1,
    progress: Callable[[str], None] | None = None,
    cancel: Any = None,
) -> LLMStats:
    """``candidates`` = [(dosya, konum, paragraf)]. Kabul edilenler builder'a taslak olarak eklenir.

    * ``version`` verilmezse istemin en yeni sürümü (``user_prompt_dir``'deki kullanıcı sürümleri dahil).
    * ``known_names`` verilmezse builder'daki (kural tabanlı okumadan gelen) adlar kullanılır;
      her çağrıya yalnız o parçada geçenler gider.
    * ``mask`` — ``True`` kişisel veriyi maskeler; LLM adresi yerel değilse her durumda maskelenir.
    * ``workers`` > 1 çağrıları paralel yapar (sonuçlar yine sırayla uygulanır);
      ``progress`` ilerleme iletisi alır; ``cancel`` (``threading.Event`` ya da çağrılabilir)
      kalan parçaları iptal eder.

    LLM hataları okumayı durdurmaz: rapora not düşülür, ``errors``'ta sayılır.
    """
    stats = LLMStats()
    by_file: dict[str, list[tuple[str, str]]] = {}
    for file, locator, text in candidates:
        by_file.setdefault(file, []).append((locator, text))
    for file in by_file:
        stats.per_file[file] = _file_counts()
    if not by_file:
        return stats

    try:
        if prompt_id != DEFAULT_PROMPT:
            raise prompt_library.PromptError(f"akış çıkarımı yalnız '{DEFAULT_PROMPT}' istemiyle yapılır, "
                                             f"'{prompt_id}' verildi")
        prompt = prompt_library.load_prompt(prompt_id, version, user_prompt_dir)
        client.config.check_allowed()
    except (prompt_library.PromptError, LLMError) as exc:
        stats.errors.append(str(exc))
        for file in by_file:
            stats.per_file[file]["errors"] += 1
            if reports.get(file):
                reports[file].note(f"LLM hatası: {str(exc)[:160]}")
        return stats

    extractor = f"llm:{client.config.model}@{prompt['id']}/v{prompt['version']}"
    stats.prompt = {"id": prompt["id"], "version": prompt["version"], "origin": prompt["origin"],
                    "extractor": extractor}
    stats.masking = should_mask(client.config, mask)
    cap = _cap(confidence_cap)
    known = [str(name) for name in known_names] if known_names is not None else _builder_names(builder)
    chunks = [chunk for file, paragraphs in by_file.items()
              for chunk in make_chunks(file, paragraphs, client.config.max_chars_per_call, mask=stats.masking)]
    stats.chunks = len(chunks)
    doc_types = {file: _doc_type(reports.get(file)) for file in by_file}
    seen: dict[str, set[tuple[Any, ...]]] = {file: set() for file in by_file}
    reasons: dict[str, dict[str, int]] = {file: {} for file in by_file}
    masked: dict[str, dict[str, int]] = {file: {} for file in by_file}
    skipped: dict[str, int] = {}
    for chunk in chunks:
        stats.per_file[chunk.file]["chunks"] += 1

    def run(chunk: Chunk) -> ChunkResult | None:
        if _is_cancelled(cancel):
            return None
        return ask_chunk(client, prompt, chunk, known_names=relevant_names(known, chunk.text),
                         doc_type=doc_types[chunk.file], confidence_cap=cap)

    done = 0
    for chunk, result in _run_all(chunks, run, max(1, int(workers or 1))):
        if result is None:
            stats.cancelled = True
            _bump(skipped, chunk.file)
            continue
        done += 1
        _collect(stats, builder, reports.get(chunk.file), result, extractor, cap,
                 seen[chunk.file], reasons[chunk.file], masked[chunk.file])
        if progress is not None:
            progress(f"LLM: {done}/{len(chunks)} parça")

    for file in by_file:
        report = reports.get(file)
        if report is None:
            continue
        counts = stats.per_file[file]
        if counts["rejected"]:
            summary = ", ".join(f"{REJECT_REASONS.get(code, code)}: {n}" for code, n in reasons[file].items())
            report.note(f"LLM önerilerinden {counts['rejected']} tanesi kanıt denetiminden geçemedi ve "
                        f"reddedildi ({summary})")
        if counts["unresolved"]:
            report.note(f"LLM {counts['unresolved']} cümlede akış buldu ama adlar eksik; onay kuyruğunda "
                        "insan incelemesi bekliyor")
        if masked[file]:
            report.note(f"LLM'e gönderilmeden önce {sum(masked[file].values())} kişisel veri maskelendi "
                        f"({describe_counts(masked[file])})")
        if skipped.get(file):
            report.note(f"LLM okuması iptal edildi; {skipped[file]} parça işlenmedi")
        if counts["columns_not_applied"]:
            report.note(f"LLM'in bulduğu {counts['columns_not_applied']} kolon eşlemesi taslak olarak "
                        "işaretlenemediği için modele eklenmedi")
    return stats


def _collect(stats: LLMStats, builder: ModelBuilder, report: DocReport | None, result: ChunkResult,
             extractor: str, cap: float, seen: set[tuple[Any, ...]], reasons: dict[str, int],
             masked: dict[str, int]) -> None:
    """Bir parçanın sonucunu istatistiğe ve (kabul edilenleri) model kurucuya işler."""
    chunk = result.chunk
    counts = stats.per_file[chunk.file]
    stats.calls += 1
    counts["calls"] += 1
    add_counts(stats.masked, chunk.masked)
    add_counts(masked, chunk.masked)
    counts["masked"] += sum(chunk.masked.values())
    if result.error is not None or result.review is None:
        message = result.error or "LLM yanıtı işlenemedi"
        stats.errors.append(message)
        counts["errors"] += 1
        if report is not None:
            report.note(f"LLM hatası: {message[:160]}")
        return

    review = result.review
    for rejection in review.rejected:
        stats.rejected += 1
        counts["rejected"] += 1
        _bump(stats.reasons, rejection["code"])
        _bump(reasons, rejection["code"])
    for drop in review.dropped:
        _bump(stats.dropped, drop["code"])
    stats.merged += review.merged
    counts["merged"] += review.merged

    new_flows = 0
    for accepted in review.accepted:
        if accepted.key in seen:  # önceki bir parçada da vardı: yalnız kaynak bilgisi eklenir
            stats.merged += 1
            counts["merged"] += 1
            continue
        seen.add(accepted.key)
        stats.accepted += 1
        counts["accepted"] += 1
        _bump(stats.items, _PLURAL[accepted.type])
        if accepted.type == "flow":
            new_flows += 1
            _bump(stats.items, "columns", len(accepted.item.columns))
    for locator, items in _group_by_locator(review.accepted):
        applied = apply_extract(builder, extract_doc(items), file=chunk.file, locator=locator,
                                extractor=extractor, draft=True, confidence_cap=cap)
        stats.columns_not_applied += applied.get("columns_skipped", 0)
        counts["columns_not_applied"] += applied.get("columns_skipped", 0)

    for entry in review.unresolved:
        key = ("unresolved", _norm(entry["text"]))
        if key in seen:
            stats.merged += 1
            counts["merged"] += 1
            continue
        seen.add(key)
        stats.unresolved.append({"file": chunk.file, "locator": entry["locator"], "text": entry["text"],
                                 "reason": entry["reason"], "extractor": extractor})
        counts["unresolved"] += 1

    if report is not None:
        report.flows += new_flows
        if review.accepted:
            report.llm_used = True

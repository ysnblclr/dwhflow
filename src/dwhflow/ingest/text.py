"""Serbest metinden (Word paragrafları, PDF, TXT, Markdown) akış çıkarma — LLM'siz.

DWH dokümanlarında tablo adları çoğunlukla ``STG_ACCOUNT``, ``DWH.F_SALES`` gibi
tanımlayıcılardır. Yön, Türkçe hâl eklerinden ve fiillerden anlaşılır:

* ``A tablosundan B tablosuna yüklenir`` / ``A'dan B'ye aktarılır`` → A → B
* ``B tablosu A tablosundan beslenir / oluşturulur`` → A → B
* ``A ve B birleştirilerek C oluşturulur`` → A → C, B → C
* ``A, B tablosunu besler`` → A → B
* ``load from A into B``, ``B is populated from A``, ``A feeds B``
* ``A -> B -> C`` ya da ``A → B`` zincirleri
* ``LOAD_X işi …`` → akış bu job üzerinden kurulur

Metinden gelen her olgu **taslak** işaretlenir ve kanıt cümlesini taşır.
Kuralın çözemediği ama iki tablo adı geçen paragraflar LLM'e aday olarak döner.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..model import DatasetKind, Layer, ProcessKind
from .base import DocReport, ModelBuilder, fold, ref
from .tables import TableView, interpret

EXTRACTOR = "doc:kural"
CONFIDENCE = 0.6

# Tanımlayıcı: harfle başlar; alt çizgi ya da şema noktası içerir, ya da tamamı
# büyük harf/rakamdır (≥3 karakter). Dosya adları (.dat/.csv…) da kabul edilir.
_IDENT = re.compile(
    r"(?<![\w.])([A-Za-z][A-Za-z0-9_$#]*(?:\.[A-Za-z0-9_$#]+){0,2})(?![\w])"
)
_STOP = {
    "SQL", "ETL", "ELT", "ODS", "DWH", "EDW", "API", "CSV", "PDF", "XML", "JSON", "UTC", "ID",
    "PII", "DQ", "SLA", "BI", "CDC", "SCD", "SCD1", "SCD2", "TPT", "MFT", "FTP", "SFTP", "ODBC",
    "JDBC", "EBCDIC", "ASCII", "UTF", "AND", "THE", "FROM", "INTO", "NOT", "VE", "ILE", "VEYA",
    "YOK", "VAR", "NULL", "TRUE", "FALSE", "OK", "NA", "TBD", "IT", "KPI", "AS", "IS", "OR",
    "BY", "ON", "TO", "IN", "OF", "FOR", "WITH", "ALL",
}
_FILE_EXT = re.compile(r"\.(dat|csv|txt|asc|ebc|bin|xml|json|gz|out|fix)$", re.IGNORECASE)

_ABLATIVE_WORDS = ("tablosundan", "dosyasindan", "kaynagindan", "sisteminden", "viewindan",
                   "view inden", "tablolarindan", "dosyalarindan")
_DATIVE_WORDS = ("tablosuna", "dosyasina", "sistemine", "tablolarina", "dosyalarina", "hedefine",
                 "dizinine", "klasorune", "ortamina", "sunucusuna", "veritabanina", "semasina",
                 "alanina")
_ACCUSATIVE_WORDS = ("tablosunu", "dosyasini", "tablolarini")
_NOMINATIVE_WORDS = ("tablosu", "dosyasi", "tablolari", "view", "tablo", "dosya")
_JOB_WORDS = ("isi", "isiyle", "job", "jobu", "jobi", "jobuyla", "jobiyla", "islemi", "islemiyle",
              "prosedur", "proseduru", "proseduruyle", "prosedurunun", "script", "scripti",
              "scriptiyle", "mapping", "mappingi", "mappingiyle", "sureci", "sureciyle",
              "procedure", "task")

_MOVE_VERBS = ("yuklen", "aktar", "tasin", "yazil", "yazar", "kopyala", "gonderil", "basil",
               "okuyup", "okur", "cekil", "load", "copied", "moved", "written", "writes",
               "transfer", "insert")
_FED_VERBS = ("beslen", "doldurul", "olusturul", "turetil", "uretil", "hesaplan", "populated",
              "derived", "built", "created", "loaded from", "sourced")
_FEEDS_VERBS = ("besler", "doldurur", "feeds", "feed")
# "merge" bilerek yok: DWH'de MERGE bir yükleme yöntemidir (upsert), birleştirme değil.
_JOIN_VERBS = ("birlestir", "joinle", "join", "combin", "eslestir")

_ARROW = re.compile(r"\s*(?:->|-->|=>|→|⇒|>>)\s*")


@dataclass
class _Token:
    name: str
    start: int
    end: int
    case: str = "nom"  # abl | dat | acc | nom
    job: bool = False
    hint: str | None = None  # system | file


@dataclass
class TextResult:
    flows: int = 0
    leftovers: list[tuple[str, str]] = field(default_factory=list)  # (konum, paragraf)


def looks_like_table(token: str) -> bool:
    """Tablo/dosya adı gibi mi? Alt çizgi, şema noktası ya da dosya uzantısı şart.

    Yalnız büyük harfli sözcükler (BDDK, MUSTERI) kurum kısaltması da olabilir;
    yanlış soy bilgisi eksik bilgiden kötü olduğu için bunlar kurala değil LLM'e kalır.
    """
    if _FILE_EXT.search(token):
        return True
    bare = token.rsplit(".", 1)[-1]
    if token.upper() in _STOP or bare.upper() in _STOP or len(bare) < 3:
        return False
    return ("_" in bare or "." in token) and any(c.isalpha() for c in bare)


def _hint(word: str) -> str | None:
    if word.startswith(("sistem", "portal", "kurum")):
        return "system"
    if word.startswith(("dosya", "dizin", "klasor")):
        return "file"
    return None


def _suffix_case(after: str) -> tuple[str | None, bool]:
    """Tanımlayıcıdan hemen sonraki metinden hâl ekini ve 'iş' işaretini çıkarır."""
    stripped = after.lstrip()
    # Kesme işaretiyle eklenen ek: STG_A'dan, STG_A’ya
    match = re.match(r"^['’]\s*([A-Za-zçğıöşüÇĞİÖŞÜ]+)", after)
    if match:
        suffix = fold(match.group(1))
        if suffix.endswith(("dan", "den", "tan", "ten")):
            return "abl", False
        if suffix.endswith(("a", "e", "ya", "ye", "na", "ne")):
            return "dat", False
        if suffix.endswith(("i", "u", "yi", "yu", "ni", "nu")):
            return "acc", False
    word = fold(stripped.split(" ", 2)[0] if stripped else "")
    two = fold(" ".join(stripped.split(" ", 3)[:2])) if stripped else ""
    if any(word.startswith(w) for w in _ABLATIVE_WORDS) or two in _ABLATIVE_WORDS:
        return "abl", False
    if any(word.startswith(w) for w in _DATIVE_WORDS):
        return "dat", False
    if any(word.startswith(w) for w in _ACCUSATIVE_WORDS):
        return "acc", False
    if word in _JOB_WORDS:
        return None, True
    if any(word == w for w in _NOMINATIVE_WORDS):
        return "nom", False
    return None, False


def _tokens(sentence: str) -> list[_Token]:
    found = []
    for match in _IDENT.finditer(sentence):
        name = match.group(1).rstrip(".")
        if not looks_like_table(name):
            continue
        case, is_job = _suffix_case(sentence[match.end():match.end() + 40])
        following = sentence[match.end():match.end() + 40].split()
        hint = _hint(fold(following[0])) if following else None
        # "job LOAD_X" gibi önde duran işaret; yalnız tam sözcük ("Akış:" değil).
        before_words = fold(sentence[max(0, match.start() - 16):match.start()]).split()
        if before_words and before_words[-1] in ("job", "mapping", "script", "procedure", "prosedur"):
            is_job = True
        found.append(_Token(name, match.start(), match.end(), case or "nom", is_job, hint))
    return found


def _english(sentence: str) -> list[tuple[str, str]]:
    pairs = []
    text = sentence
    for pattern, order in (
        (r"from\s+([\w.$#]+)\s+(?:in)?to\s+([\w.$#]+)", "st"),
        (r"([\w.$#]+)\s+(?:is|are)\s+(?:loaded|copied|moved|written|transferred)\s+(?:in)?to\s+([\w.$#]+)", "st"),
        (r"([\w.$#]+)\s+(?:is|are)\s+(?:populated|derived|built|created|loaded|sourced)\s+from\s+([\w.$#]+)", "ts"),
        (r"([\w.$#]+)\s+feeds?\s+([\w.$#]+)", "st"),
        (r"reads?\s+(?:from\s+)?([\w.$#]+).{0,60}?(?:writes?|loads?|inserts?)\s+"
         r"(?:it\s+|them\s+|the\s+result\s+)?(?:in)?to\s+([\w.$#]+)", "st"),
    ):
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            a, b = match.group(1).rstrip(".,;:"), match.group(2).rstrip(".,;:")
            if looks_like_table(a) and looks_like_table(b):
                pairs.append((a, b) if order == "st" else (b, a))
    return pairs


def sentence_hints(sentence: str) -> dict[str, str]:
    """Ad → ipucu ("system" / "file"): "X sistemine", "Y dosyasından" gibi."""
    return {t.name: t.hint for t in _tokens(sentence) if t.hint}


def extract_sentence(sentence: str) -> tuple[list[tuple[str, str]], str | None]:
    """Bir cümleden (kaynak, hedef) çiftlerini ve varsa job adını çıkarır."""
    folded = fold(sentence)
    tokens = _tokens(sentence)
    jobs = [t for t in tokens if t.job]
    job = jobs[0].name if jobs else None
    tables = [t for t in tokens if not t.job]

    # Ok zinciri: A -> B -> C
    if _ARROW.search(sentence):
        parts = [p.strip() for p in _ARROW.split(sentence)]
        chain = []
        for part in parts:
            names = [m.group(1) for m in _IDENT.finditer(part) if looks_like_table(m.group(1))]
            if names:
                chain.append(names[-1] if chain else names[-1])
        if len(chain) >= 2:
            return list(zip(chain, chain[1:], strict=False)), job

    english = _english(sentence)
    if english:
        return english, job
    if len(tables) < 2:
        return [], job

    abl = [t.name for t in tables if t.case == "abl"]
    dat = [t.name for t in tables if t.case == "dat"]
    acc = [t.name for t in tables if t.case == "acc"]
    nom = [t.name for t in tables if t.case == "nom"]

    if any(v in folded for v in _JOIN_VERBS):
        targets = dat or ([nom[-1]] if nom else [])
        sources = [t.name for t in tables if t.name not in targets]
        return [(s, t) for t in targets for s in sources], job
    if abl and dat:
        return [(s, t) for s in abl for t in dat], job
    if abl and any(v in folded for v in _FED_VERBS):
        targets = [n for n in nom if n not in abl] or dat
        return [(s, t) for s in abl for t in targets], job
    if any(v in folded for v in _FEEDS_VERBS) and (acc or dat):
        sources = [t.name for t in tables if t.case == "nom"][:1] or [tables[0].name]
        return [(s, t) for s in sources for t in (acc or dat)], job
    if dat and any(v in folded for v in _MOVE_VERBS):
        sources = [n for n in nom] or abl
        return [(s, t) for s in sources for t in dat if s != t], job
    return [], job


def _hint_attrs(hint: str | None, side: str) -> dict:
    """"X sistemine gönderilir" → X dış sistem; "Y sisteminden alınır" → Y kaynak."""
    if hint == "system":
        return ({"layer": Layer.SOURCE, "kind": DatasetKind.SOURCE_OBJECT} if side == "source"
                else {"layer": Layer.CONSUMPTION, "kind": DatasetKind.DOWNSTREAM_SYSTEM})
    if hint == "file":
        return {"layer": Layer.INTERFACE, "kind": DatasetKind.INTERFACE_FILE}
    return {}


_SENTENCE = re.compile(r"(?<=[.!?;])\s+(?=[A-ZÇĞİÖŞÜ0-9\"'(])|\n+")


def sentences(paragraph: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(paragraph) if s and s.strip()]


def extract_paragraphs(paragraphs: list[tuple[str, str]], builder: ModelBuilder,
                       report: DocReport, *, file: str) -> TextResult:
    """(konum, paragraf) listesinden akış çıkarır; çözülemeyen adayları döner."""
    result = TextResult()
    for locator, paragraph in paragraphs:
        found_here = 0
        for sentence in sentences(paragraph):
            pairs, job = extract_sentence(sentence)
            if not pairs:
                continue
            source = ref(file, locator, EXTRACTOR, confidence=CONFIDENCE, evidence=sentence)
            via = builder.process(job, source, draft=True, kind=ProcessKind.ETL_MAPPING) if job else None
            hints = sentence_hints(sentence)
            for a, b in pairs:
                a_key = builder.dataset(a, source, draft=True, **_hint_attrs(hints.get(a), "source"))
                b_key = builder.dataset(b, source, draft=True, **_hint_attrs(hints.get(b), "target"))
                if builder.flow(a_key, b_key, via=via, source=source, draft=True):
                    found_here += 1
        result.flows += found_here
        if not found_here:
            names = {m.group(1) for m in _IDENT.finditer(paragraph) if looks_like_table(m.group(1))}
            if len(names) >= 2:
                result.leftovers.append((locator, paragraph))
    return result


# --------------------------------------------------------------------------- #
# Markdown / düz metin
# --------------------------------------------------------------------------- #

_MD_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_MD_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}")


def split_markdown(text: str) -> tuple[list[TableView], list[tuple[str, str]]]:
    """Markdown/TXT: boru (|) tablolarını ve paragrafları ayırır."""
    tables: list[TableView] = []
    paragraphs: list[tuple[str, str]] = []
    lines = text.splitlines()
    buffer: list[str] = []
    buffer_start = 1
    heading = ""
    index = 0

    def flush(end_line: int) -> None:
        nonlocal buffer
        if buffer:
            where = f"{heading} · satır {buffer_start}" if heading else f"satır {buffer_start}"
            paragraphs.append((where, " ".join(buffer)))
            buffer = []

    while index < len(lines):
        line = lines[index]
        if _MD_TABLE_ROW.match(line):
            flush(index)
            block_start = index + 1
            block = []
            while index < len(lines) and _MD_TABLE_ROW.match(lines[index]):
                if not _MD_SEPARATOR.match(lines[index]):
                    block.append([c.strip() for c in lines[index].strip().strip("|").split("|")])
                index += 1
            if len(block) >= 2:
                tables.append(TableView(headers=block[0], rows=block[1:],
                                        locator=f"tablo (satır {block_start})",
                                        row_locator="satır {n}"))
            continue
        stripped = line.strip()
        if stripped.startswith("#"):
            flush(index)
            heading = stripped.lstrip("#").strip()[:60]
        elif not stripped:
            flush(index)
        else:
            if not buffer:
                buffer_start = index + 1
            buffer.append(stripped.lstrip("-*•> ").strip())
        index += 1
    flush(index)
    return tables, paragraphs


def read_text(text: str, builder: ModelBuilder, report: DocReport, *, file: str) -> TextResult:
    tables, paragraphs = split_markdown(text)
    flows = 0
    for table in tables:
        flows += interpret(table, builder, report, file=file, reader="doc:tablo")
    result = extract_paragraphs(paragraphs, builder, report, file=file)
    result.flows += flows
    return result

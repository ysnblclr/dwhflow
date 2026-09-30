"""ETL ve zamanlayıcı okuyucularının ortak yardımcıları.

* Boyut sınırı: kullanıcı dosyası ``MAX_BYTES``'tan büyükse okunmaz, raporda hata olur.
* Güvenli XML: yüklenen XML **yalnız** ``defusedxml`` ile ayrıştırılır (varlık genişletme,
  dış varlık ve XXE saldırıları engellenir).
* Gömülü SQL: DataStage stage'lerindeki ve Airflow görevlerindeki SQL, ``readers._lineage``
  ile çözülür; salt okuma (SELECT) ifadelerinin tabloları da çıkarılır.
* Komut satırı: parolalar açıklamaya yazılmadan önce maskelenir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException

from ..base import DocReport
from ..readers import _DIALECTS, _DOT_COMMAND, _decode, _lineage, _statements, _table_name

if TYPE_CHECKING:  # yalnız tür bilgisi; ayrıştırma her zaman defusedxml ile yapılır
    from xml.etree.ElementTree import Element

#: Okunacak en büyük dosya (bayt).
MAX_BYTES = 50 * 1024 * 1024
#: Çözülemeyen parametre/şablon yerine SQL'e konan tanıtıcı; bu adı taşıyan tablolar atılır.
PLACEHOLDER = "DWHFLOW_COZULEMEYEN"
DEEP_MESSAGE = "dosya aşırı derin iç içe yapı içeriyor; okunamadı"


# --------------------------------------------------------------------------- #
# Dosya okuma
# --------------------------------------------------------------------------- #


def read_capped(path: Path, report: DocReport, limit: int | None = None) -> bytes | None:
    """Dosyayı en çok ``limit`` (varsayılan ``MAX_BYTES``) bayt okur; büyükse raporu hata yapar ve None döner."""
    limit = MAX_BYTES if limit is None else limit
    try:
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError as exc:
        report.status = "error"
        report.note(f"dosya okunamadı: {exc}")
        return None
    if len(data) > limit:
        report.status = "error"
        report.note(f"dosya çok büyük; en çok {limit // (1024 * 1024)} MB okunur")
        return None
    return data


def decode(data: bytes) -> str:
    return _decode(data)


def strip_bom(text: str) -> str:
    return text[1:] if text.startswith("﻿") else text


def sniff_text(head: bytes) -> str:
    """İçerik tespiti için dosya başını metne çevirir (UTF-16 BOM'u tanınır, hatalar yutulur)."""
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return head.decode("utf-16", errors="ignore").lstrip("﻿")
    return strip_bom(head.decode("utf-8", errors="ignore"))


# --------------------------------------------------------------------------- #
# Güvenli XML
# --------------------------------------------------------------------------- #


class XmlError(ValueError):
    """XML okunamadı; ``str(exc)`` kullanıcıya gösterilecek Türkçe açıklamadır."""


_XML_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>")


def parse_xml(data: bytes | str) -> Element:
    """``defusedxml`` ile ayrıştırır.

    Bayt olarak okunamazsa (bildirimsiz Türkçe cp1254 baytlar, bilinmeyen/desteklenmeyen kodlama
    bildirimi) metne çevrilip XML bildirimi atılarak bir kez daha denenir. Varlık/DTD saldırısı
    (ör. "billion laughs") her iki denemede de ``XmlError`` olur; genişletme yapılmaz.
    """
    unsafe = "güvenli olmayan XML yapısı reddedildi ({})"
    try:
        return SafeET.fromstring(data)
    except DefusedXmlException as exc:
        raise XmlError(unsafe.format(type(exc).__name__)) from None
    except (SafeET.ParseError, LookupError, ValueError) as exc:
        first = exc
    text = data if isinstance(data, str) else strip_bom(decode(data))
    try:
        return SafeET.fromstring(_XML_DECLARATION.sub("", text, count=1))
    except DefusedXmlException as exc:
        raise XmlError(unsafe.format(type(exc).__name__)) from None
    except (SafeET.ParseError, LookupError, ValueError):
        raise XmlError(f"XML ayrıştırılamadı: {first}") from None


def local(tag: object) -> str:
    """Ad alanı önekini atar: ``{http://x}JOB`` → ``JOB``."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def xml_to_text(element: Element) -> str:
    return SafeET.tostring(element, encoding="unicode")


_OPAQUE = re.compile(r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>", re.S)


def _mask_opaque(text: str) -> str:
    """Yorum, CDATA ve işlem talimatlarını (satır sonlarını koruyarak) boşlukla örter."""
    return _OPAQUE.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), text)


def _start_tag_end(text: str, index: int) -> int | None:
    """Açılış etiketinin bittiği yerin (``>`` sonrası) konumu; tırnak içindeki ``>`` atlanır."""
    quote = None
    for position in range(index, len(text)):
        char = text[position]
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == ">":
            return position + 1
    return None


def element_spans(text: str, tag: str) -> list[tuple[int, int, int]]:
    """Ham XML metninde ``tag`` öğelerinin (başlangıç, bitiş, satır) konumları, belge sırasıyla.

    Önizlemede öğenin ham metnini ve satır numarasını göstermek içindir; iç içe aynı adlı
    öğeler desteklenmez (DataStage ``Job`` ve Control-M ``JOB`` iç içe olmaz).
    """
    masked = _mask_opaque(text)
    opening = re.compile(rf"<{re.escape(tag)}(?=[\s/>])")
    closing = re.compile(rf"</{re.escape(tag)}\s*>")
    spans: list[tuple[int, int, int]] = []
    position, line, counted = 0, 1, 0
    while True:
        match = opening.search(masked, position)
        if not match:
            break
        head_end = _start_tag_end(masked, match.end())
        if head_end is None:
            break
        if masked[head_end - 2] == "/":
            end = head_end
        else:
            close = closing.search(masked, head_end)
            if not close:
                break
            end = close.end()
        line += masked.count("\n", counted, match.start())
        counted = match.start()
        spans.append((match.start(), end, line))
        position = end
    return spans


# --------------------------------------------------------------------------- #
# Gömülü SQL
# --------------------------------------------------------------------------- #

_LEADING_NOISE = re.compile(r"(?:\s|--[^\n]*(?:\n|$)|/\*.*?\*/)*", re.S)
_LOCKING = re.compile(
    r"^LOCK(?:ING)?\b[^;]*?\bFOR\s+(?:ACCESS|READ|WRITE|EXCLUSIVE|EXCL|CHECKSUM)(?:\s+MODE)?(?:\s+NOWAIT)?\s+",
    re.I | re.S)
_SQL_START = re.compile(
    r"^(?:\(|select|sel|with|insert|ins|update|upd|merge|delete|del|create|replace|upsert|call|exec|"
    r"execute|locking|lock)\b", re.I)
_READ_START = re.compile(r"^(?:\(|select|sel|with)\b", re.I)
_WRITE_START = re.compile(r"^(?:insert|ins|update|upd|merge|create|replace|upsert)\b", re.I)


@dataclass
class SqlStatement:
    """Bir SQL ifadesinin soyu: ``target`` yazılan tablo (salt okumada None), ``sources`` okunanlar."""

    target: str | None
    sources: list[str]


@dataclass
class SqlResult:
    statements: list[SqlStatement] = field(default_factory=list)
    #: Veri yazan ya da okuyan ama ayrıştırılamayan ifade sayısı.
    unparsed: int = 0
    #: Adı parametreye/şablona bağlı olduğu için atılan tablo sayısı.
    unresolved: int = 0


def looks_like_sql(text: str) -> bool:
    return bool(_SQL_START.match(_lead(text)))


def _lead(statement: str) -> str:
    """Baştaki boşluk ve yorumları atar."""
    match = _LEADING_NOISE.match(statement)
    return statement[match.end():] if match else statement


def _query_tables(statement: str) -> list[str] | None:
    """Salt okuma sorgusunun tabloları (CTE adları hariç); ayrıştırılamazsa None."""
    import sqlglot
    from sqlglot import exp

    for dialect in _DIALECTS:
        try:
            tree = sqlglot.parse_one(statement, read=dialect)
        except Exception:  # noqa: BLE001 - sonraki lehçeyi dene
            continue
        if tree is None or isinstance(tree, exp.Command):
            continue
        ctes = {cte.alias_or_name.upper() for cte in tree.find_all(exp.CTE)}
        names: list[str] = []
        for table in tree.find_all(exp.Table):
            name = _table_name(table)
            if name and name.upper() not in ctes and name not in names:
                names.append(name)
        return names
    return None


def analyze_sql(text: str) -> SqlResult:
    """SQL metnini ifadelere böler ve her ifadenin yazdığı/okuduğu tabloları çıkarır.

    Adında ``PLACEHOLDER`` geçen tablolar (çözülemeyen parametre/şablon) sayılıp atılır.
    """
    result = SqlResult()

    def keep(name: str | None) -> bool:
        if not name:
            return False
        if PLACEHOLDER in name.upper():
            result.unresolved += 1
            return False
        return True

    for raw in _statements(_DOT_COMMAND.sub("", text)):
        statement = _lead(raw)
        if not _SQL_START.match(statement):
            continue
        statement = _LOCKING.sub("", statement, count=1)
        lineage = _lineage(statement)
        if lineage is not None:
            target, sources, _ = lineage
            kept_sources = [s for s in sources if keep(s)]
            kept_target = target if keep(target) else None
            if kept_target or kept_sources:
                result.statements.append(SqlStatement(kept_target, kept_sources))
            continue
        if _READ_START.match(statement):
            tables = _query_tables(statement)
            if tables is None:
                result.unparsed += 1
                continue
            kept = [t for t in tables if keep(t)]
            if kept:
                result.statements.append(SqlStatement(None, kept))
        elif _WRITE_START.match(statement):
            result.unparsed += 1
    return result


# --------------------------------------------------------------------------- #
# Komut satırı
# --------------------------------------------------------------------------- #

_SECRETS = (
    (re.compile(r"(?i)(-{1,2}(?:password|passwd|pwd|pass|pw)(?:=|\s+))(\S+)"), r"\1***"),
    (re.compile(r"(?i)\b((?:password|passwd|pwd)=)(\S+)"), r"\1***"),
    (re.compile(r"(?<![/\w])([\w.$%-]+)/([^\s@/]+)@"), r"\1/***@"),
)


def mask_secrets(command: str) -> str:
    """Komut satırındaki parolaları maskeler (``-password x``, ``kullanici/parola@db``)."""
    for pattern, replacement in _SECRETS:
        command = pattern.sub(replacement, command)
    return command


def shorten(text: str, limit: int = 200) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"

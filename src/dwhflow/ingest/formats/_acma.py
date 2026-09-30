"""Arşiv ve e-posta açma: ``.zip`` (iç içe), ``.eml`` ve — ``extract-msg`` kuruluysa — ``.msg``.

Açılan her dosya ayrı bir doküman olarak okunur (``ingest._expand``). Kurallar:

* **Güvenlik (en üst düzey arşiv):** mutlak yol ya da ``..`` içeren öğe (zip-slip), şifreli
  öğe, olağandışı sıkıştırma oranı (zip bombası) → arşivin tamamı reddedilir ve raporda
  nedeniyle görünür. Hiçbir dosya çalışma dizini dışına yazılmaz: öğeler **düz** yazılır
  (yalnız temizlenmiş dosya adı kullanılır; klasör yapısı atılır), aynı ad ikinci kez
  gelirse ``_2/``, ``_3/`` alt klasörüne konur. Özgün dosya adı korunur.
* **Sınırlar (tüm iç içe düzeyler toplamı):** en çok ``MAX_FILES`` dosya ve ``MAX_TOTAL_BYTES``
  açılmış boyut; aşılırsa arşivin tamamı reddedilir.
* **Atlananlar:** klasörler, sembolik bağlar, ``__MACOSX``, nokta ile başlayan adlar,
  ``Thumbs.db``, ``desktop.ini``, Office kilit dosyaları (``~$…``).
* **İç içe:** arşiv/e-posta içindeki ``.zip``/``.eml``/``.msg`` en çok ``MAX_DEPTH`` düzeye
  kadar açılır. Daha derindeki ya da bozuk/güvensiz iç arşiv **açılmadan** dosya olarak
  bırakılır; kendi okuyucusu nedenini raporlar (sessiz atlama yok).
* **E-posta:** gövde (düz metin tercih edilir, yoksa HTML) başında Konu/Kimden/Tarih ile
  ``<ad>.govde.txt`` ya da ``.html`` olarak, her ek kendi adıyla yazılır. Satır içi
  görseller (imza logoları) atlanır; iletilen e-postalar (``message/rfc822``) iç içe açılır.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import html
import re
import shutil
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import Message
from pathlib import Path
from typing import Any

from ..readers import _decode
from ._ortak import FormatError, module_available, one_line, read_limited

MAX_FILES = 500
MAX_TOTAL_BYTES = 200 * 1024 * 1024
MAX_DEPTH = 3
#: Açılmış/sıkıştırılmış oranı bunu aşan ve ``RATIO_MIN_BYTES``'tan büyük öğe bomba sayılır.
MAX_RATIO = 200
RATIO_MIN_BYTES = 1024 * 1024
MAX_MAIL_BYTES = 100 * 1024 * 1024
_CHUNK = 1024 * 1024


class ArsivHatasi(ValueError):
    """Arşiv güvenli biçimde açılamadı; ileti raporda gösterilir."""


class SinirAsildi(ArsivHatasi):
    """Toplam dosya/boyut sınırı aşıldı: iç içe düzeyde de tüm arşivi durdurur."""


@dataclass
class Budget:
    files: int = 0
    total: int = 0

    def take_file(self) -> None:
        self.files += 1
        if self.files > MAX_FILES:
            raise SinirAsildi(f"arşivde {MAX_FILES}'den fazla dosya var")

    def take_bytes(self, size: int) -> None:
        self.total += size
        if self.total > MAX_TOTAL_BYTES:
            raise SinirAsildi(f"açılmış toplam boyut {MAX_TOTAL_BYTES // 2**20} MB sınırını aşıyor")


# --------------------------------------------------------------------------- #
# Dosya adları
# --------------------------------------------------------------------------- #

_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}


def clean_part(name: str, fallback: str = "dosya") -> str:
    """Tek bir dosya adını her işletim sisteminde güvenli hâle getirir (yol ayırıcısı kalmaz)."""
    value = _BAD_CHARS.sub("_", str(name)).strip().rstrip(". ")
    if not value or set(value) <= {".", " "}:
        value = fallback
    if value.split(".", 1)[0].lower() in _RESERVED:
        value = f"_{value}"
    if len(value) > 120:
        stem, dot, ext = value.rpartition(".")
        value = f"{stem[:110]}.{ext}" if dot and 0 < len(ext) <= 8 else value[:120]
    return value


def place_file(dest: Path, name: str) -> Path:
    """``dest/ad``; ad alınmışsa ``dest/_2/ad`` … (özgün ad korunur)."""
    candidate, number = dest / name, 2
    while candidate.exists():
        folder = dest / f"_{number}"
        number += 1
        if folder.exists() and not folder.is_dir():
            continue
        candidate = folder / name
    candidate.parent.mkdir(parents=True, exist_ok=True)
    if not candidate.resolve().is_relative_to(dest.resolve()):
        raise ArsivHatasi(f"güvensiz dosya adı: {name!r}")
    return candidate


def fresh_dir(parent: Path, name: str) -> Path:
    base = clean_part(name)
    candidate, number = parent / base, 2
    while candidate.exists():
        candidate = parent / f"{base}_{number}"
        number += 1
    candidate.mkdir(parents=True)
    return candidate


# --------------------------------------------------------------------------- #
# Zip
# --------------------------------------------------------------------------- #


def entry_name(info: zipfile.ZipInfo) -> str:
    """UTF-8 bayrağı olmayan adlar: önce UTF-8, olmazsa Türkçe DOS kod sayfası (cp857)."""
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    for encoding in ("utf-8", "cp857"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return name


def split_entry(name: str) -> list[str]:
    value = name.replace("\\", "/")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise ArsivHatasi(f"arşivde mutlak yol var: {name!r}")
    parts = [part for part in value.split("/") if part not in ("", ".")]
    if any(part.strip() == ".." for part in parts):
        raise ArsivHatasi(f"arşivde üst klasöre çıkan yol var (zip-slip): {name!r}")
    return parts


def _junk(parts: list[str]) -> bool:
    last = parts[-1]
    return (any(part == "__MACOSX" or part.startswith(".") for part in parts)
            or last.lower() in ("thumbs.db", "desktop.ini") or last.startswith("~$"))


def _symlink(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def plan_zip(archive: zipfile.ZipFile, budget: Budget) -> list[tuple[zipfile.ZipInfo, str]]:
    """Tüm öğeleri **yazmadan önce** denetler; okunacak (öğe, dosya adı) listesi."""
    plan: list[tuple[zipfile.ZipInfo, str]] = []
    for info in archive.infolist():
        name = entry_name(info)
        parts = split_entry(name)
        if info.is_dir() or not parts or _symlink(info) or _junk(parts):
            continue
        if info.flag_bits & 0x1:
            raise ArsivHatasi(f"şifreli öğe var ({name}); şifresiz arşiv yükleyin")
        if info.file_size > RATIO_MIN_BYTES and info.file_size > MAX_RATIO * max(info.compress_size, 1):
            raise ArsivHatasi(f"'{name}' için sıkıştırma oranı olağandışı yüksek (zip bombası olabilir)")
        plan.append((info, clean_part(parts[-1])))
    if budget.files + len(plan) > MAX_FILES:
        raise SinirAsildi(f"arşivde {MAX_FILES}'den fazla dosya var")
    declared = sum(info.file_size for info, _ in plan)
    if budget.total + declared > MAX_TOTAL_BYTES:
        raise SinirAsildi(f"açılmış toplam boyut {MAX_TOTAL_BYTES // 2**20} MB sınırını aşıyor")
    return plan


def _extract(archive: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path, budget: Budget) -> None:
    try:
        with archive.open(info) as source, target.open("wb") as sink:
            while chunk := source.read(_CHUNK):
                budget.take_bytes(len(chunk))
                sink.write(chunk)
    except NotImplementedError as exc:
        raise ArsivHatasi(f"'{info.filename}' desteklenmeyen sıkıştırma yöntemiyle paketlenmiş") from exc
    except (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, OSError) as exc:
        raise ArsivHatasi(f"'{info.filename}' açılamadı (arşiv bozuk): {exc}") from exc


def open_archive(path: Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise ArsivHatasi(f"zip dosyası bozuk ya da zip değil: {exc}") from exc


def _zip(path: Path, dest: Path, budget: Budget, depth: int) -> list[Path]:
    with open_archive(path) as archive:
        plan = plan_zip(archive, budget)
        out: list[Path] = []
        for info, name in plan:
            budget.take_file()
            target = place_file(dest, name)
            _extract(archive, info, target, budget)
            out.extend(expand_file(target, budget, depth + 1))
    return out


# --------------------------------------------------------------------------- #
# E-posta
# --------------------------------------------------------------------------- #


@dataclass
class Mail:
    subject: str = ""
    sender: str = ""
    date: str = ""
    body: str = ""
    html: bool = False
    #: (dosya adı, içerik) — içerik bayt ya da iç içe e-posta
    attachments: list[tuple[str, Any]] = field(default_factory=list)


def _header(message: Message, name: str) -> str:
    try:
        value = message.get(name)
    except Exception:  # noqa: BLE001 - hatalı başlık okumayı düşürmesin
        return ""
    return one_line(str(value)) if value else ""


def _sender(value: str) -> str:
    """Görünen ad varsa yalnız o (adres kişisel veridir ve kurallarda gürültü yapar)."""
    name, address = email.utils.parseaddr(value)
    return one_line(name or address or value)


def _part_text(part: Message) -> str:
    try:
        content = part.get_content()  # type: ignore[attr-defined]
        if isinstance(content, str):
            return content
    except (LookupError, UnicodeError, KeyError, ValueError, AttributeError):
        pass
    payload = part.get_payload(decode=True)
    return _decode(payload) if isinstance(payload, bytes) else ""


def _leaves(part: Message):
    if part.get_content_type() == "message/rfc822":
        yield part
        return
    if part.is_multipart():
        payload = part.get_payload()
        for sub in payload if isinstance(payload, list) else []:
            yield from _leaves(sub)
        return
    yield part


#: Adı olmayan ekler için uzantı (``mimetypes`` Windows'ta kayıt defterine göre değişir).
_EXTENSIONS = {
    "text/plain": ".txt", "text/html": ".html", "text/csv": ".csv", "text/rtf": ".rtf",
    "application/rtf": ".rtf", "application/pdf": ".pdf", "application/zip": ".zip",
    "application/xml": ".xml", "text/xml": ".xml", "application/json": ".json",
    "application/msword": ".doc", "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
}


def _attachment(part: Message, index: int) -> tuple[str, bytes] | None:
    try:
        filename = part.get_filename()
    except Exception:  # noqa: BLE001
        filename = None
    content_type = part.get_content_type()
    if content_type == "message/rfc822":
        payload = part.get_payload()
        inner = payload[0] if isinstance(payload, list) and payload else None
        if not isinstance(inner, Message):
            return None
        try:
            data = inner.as_bytes(policy=email.policy.default)
        except Exception:  # noqa: BLE001
            data = bytes(str(inner), "utf-8", errors="replace")
        name = filename or f"{_header(inner, 'Subject') or f'ek{index}'}.eml"
        return (name if name.lower().endswith(".eml") else f"{name}.eml"), data
    disposition = part.get_content_disposition()
    if not filename and disposition != "attachment":
        return None  # seçilmeyen gövde alternatifi ya da satır içi metin
    if part.get_content_maintype() == "image" and disposition != "attachment":
        return None  # imza logosu, satır içi görsel
    data = part.get_payload(decode=True)
    if not isinstance(data, bytes):
        return None
    return filename or f"ek{index}{_EXTENSIONS.get(content_type, '.bin')}", data


def mail_from_message(message: Message) -> Mail:
    mail = Mail(subject=_header(message, "Subject"), sender=_sender(_header(message, "From")),
                date=_header(message, "Date"))
    try:
        body = message.get_body(preferencelist=("plain", "html"))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        body = None
    if body is not None:
        mail.body = _part_text(body).replace("\r\n", "\n")
        mail.html = body.get_content_subtype() == "html"
    for index, part in enumerate(_leaves(message), start=1):
        if part is body:
            continue
        found = _attachment(part, index)
        if found is not None:
            mail.attachments.append(found)
    return mail


def parse_eml(data: bytes) -> Mail:
    return mail_from_message(email.message_from_bytes(data, policy=email.policy.default))


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return _decode(value)
    return str(value)


def _mail_from_msg(message: Any, level: int) -> Mail:
    """``extract_msg`` nesnesinden (yalnız belgelenmiş öznitelikler, savunmacı)."""
    mail = Mail(subject=one_line(_text(getattr(message, "subject", ""))),
                sender=_sender(one_line(_text(getattr(message, "sender", "")))),
                date=one_line(_text(getattr(message, "date", ""))))
    body = _text(getattr(message, "body", None))
    if body.strip():
        mail.body = body.replace("\r\n", "\n")
    else:
        mail.body, mail.html = _text(getattr(message, "htmlBody", None)), True
    for index, attachment in enumerate(getattr(message, "attachments", None) or [], start=1):
        name = (getattr(attachment, "longFilename", None) or getattr(attachment, "shortFilename", None)
                or getattr(attachment, "name", None) or f"ek{index}")
        data = getattr(attachment, "data", None)
        if isinstance(data, (bytes, bytearray)):
            mail.attachments.append((str(name), bytes(data)))
        elif data is not None and hasattr(data, "attachments") and level < MAX_DEPTH:
            mail.attachments.append((str(name), _mail_from_msg(data, level + 1)))
    return mail


def parse_msg(path: Path) -> Mail:
    import extract_msg

    message = extract_msg.openMsg(str(path))
    try:
        return _mail_from_msg(message, 1)
    finally:
        close = getattr(message, "close", None)
        if callable(close):
            close()


def render_mail(mail: Mail) -> tuple[str, str]:
    """(uzantı, içerik): başta Konu/Kimden/Tarih, ardından gövde."""
    fields = [(label, value) for label, value in
              (("Konu", mail.subject), ("Kimden", mail.sender), ("Tarih", mail.date)) if value]
    title = f"E-posta: {mail.subject}" if mail.subject else "E-posta"
    if mail.html:
        head = f"<h1>{html.escape(title)}</h1>" + "".join(
            f"<p>{html.escape(label)}: {html.escape(value)}</p>" for label, value in fields)
        return ".html", f"{head}\n{mail.body}"
    lines = [f"# {title}", ""]
    for label, value in fields:
        lines += [f"{label}: {value}", ""]
    return ".txt", "\n".join(lines) + "\n" + mail.body


def _write_mail(mail: Mail, stem: str, dest: Path, budget: Budget, depth: int) -> list[Path]:
    suffix, content = render_mail(mail)
    data = content.encode("utf-8")
    budget.take_file()
    budget.take_bytes(len(data))
    body = place_file(dest, clean_part(f"{stem}.govde{suffix}"))
    body.write_bytes(data)
    out = [body]
    for index, (name, payload) in enumerate(mail.attachments, start=1):
        if isinstance(payload, Mail):
            if depth + 1 > MAX_DEPTH:
                continue
            inner = fresh_dir(dest, f"{clean_part(name, f'ek{index}')}_icerik")
            out.extend(_write_mail(payload, Path(clean_part(name, f"ek{index}")).stem, inner, budget,
                                   depth + 1))
            continue
        budget.take_file()
        budget.take_bytes(len(payload))
        target = place_file(dest, clean_part(name, f"ek{index}"))
        target.write_bytes(payload)
        out.extend(expand_file(target, budget, depth + 1))
    return out


def read_mail(path: Path) -> Mail:
    """Dosyadan e-posta (``.eml`` ya da ``.msg``)."""
    if path.suffix.lower() == ".msg":
        return parse_msg(path)
    try:
        return parse_eml(read_limited(path, MAX_MAIL_BYTES))
    except FormatError as exc:
        raise ArsivHatasi(str(exc)) from exc


def _mail(path: Path, dest: Path, budget: Budget, depth: int) -> list[Path]:
    return _write_mail(read_mail(path), path.stem, dest, budget, depth)


# --------------------------------------------------------------------------- #
# Dağıtım
# --------------------------------------------------------------------------- #

Opener = Callable[[Path, Path, Budget, int], list[Path]]


def openers() -> dict[str, Opener]:
    found: dict[str, Opener] = {".zip": _zip, ".eml": _mail}
    if module_available("extract_msg"):
        found[".msg"] = _mail
    return found


def expand_file(path: Path, budget: Budget, depth: int) -> list[Path]:
    """İç içe kapsayıcıysa açar (sınır içinde); değilse ya da açılamazsa dosyanın kendisi."""
    opener = openers().get(path.suffix.lower())
    if opener is None or depth > MAX_DEPTH:
        return [path]
    inner = fresh_dir(path.parent, f"{path.name}_icerik")
    try:
        return opener(path, inner, budget, depth)
    except SinirAsildi:
        raise
    except Exception:  # noqa: BLE001 - bozuk iç arşiv: kendi okuyucusu nedenini raporlar
        shutil.rmtree(inner, ignore_errors=True)
        return [path]


def expand_top(path: Path, workdir: Path) -> list[Path]:
    """Kayıtlı ``expander``: en üst düzey arşiv/e-posta. Güvenlik ihlali tüm arşivi reddeder."""
    opener = openers().get(path.suffix.lower())
    if opener is None:
        raise ArsivHatasi(f"'{path.suffix}' açılamıyor")
    return opener(path, workdir, Budget(), 1)

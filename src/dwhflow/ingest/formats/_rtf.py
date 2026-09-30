"""RTF → doküman öğeleri: küçük ama sağlam bir RTF metin çıkarıcısı (ek bağımlılık yok).

Desteklenenler: denetim sözcükleri ve gruplar, yok sayılan hedefler (``\\*``, yazı tipi/renk
tabloları, resimler, üst/alt bilgi, alan kodları — alan *sonucu* korunur), ``\\'hh`` kaçışları
(``\\ansicpg`` kod sayfasıyla; belirtilmemişse cp1254), ``\\uN`` Unicode karakterleri
(``\\ucN`` yedek karakter atlama ile), ``\\binN`` ikili veri atlama, tablolar
(``\\cell``/``\\row`` → tablo), başlıklar (stil sayfasında "heading/başlık" adlı stil,
``\\outlinelevel`` ya da numaralı başlık satırı).
"""

from __future__ import annotations

import codecs
import re

from ..base import fold
from ._ortak import FormatError, Item, looks_like_heading, squeeze

_TOKEN = re.compile(
    r"\\([a-zA-Z]{1,32})(-?\d{1,10})? ?"   # denetim sözcüğü (+ sayı, + ayırıcı boşluk)
    r"|\\'([0-9a-fA-F]{2})"                  # onaltılık bayt
    r"|\\(.)"                                # denetim simgesi
    r"|([{}])"
    r"|([\r\n]+)"
    r"|([^\\{}\r\n]+)",
    re.DOTALL,
)

_SKIP_DESTINATIONS = frozenset({
    "fonttbl", "colortbl", "info", "pict", "object", "objdata", "objclass", "header", "headerl",
    "headerr", "headerf", "footer", "footerl", "footerr", "footerf", "footnote", "annotation",
    "atnid", "atnauthor", "atndate", "fldinst", "xmlnstbl", "listtable", "listoverridetable",
    "listtext", "pntext", "pntxta", "pntxtb", "rsidtbl", "generator", "themedata",
    "colorschememapping", "datastore", "latentstyles", "mmathpr", "filetbl", "revtbl", "private",
    "userprops", "docvar", "fchars", "lchars", "bkmkstart", "bkmkend", "xe", "tc", "txe", "shp",
    "shpinst", "nonshppict", "shppict", "blipuid", "wgrffmtfilter", "pgdsctbl", "ftnsep",
    "ftnsepc", "ftncn", "aftnsep", "aftnsepc", "aftncn", "template", "operator", "author", "title",
    "subject", "keywords", "comment", "doccomm", "company", "category", "passwordhash",
    "protusertbl", "listpicture", "defchp", "defpap", "pnseclvl", "background", "formfield",
    "datafield", "do", "sp", "sv", "sn",
})
_SPECIAL = {
    "tab": " ", "emdash": "\u2014", "endash": "\u2013", "bullet": "\u2022", "lquote": "\u2018",
    "rquote": "\u2019", "ldblquote": "\u201c", "rdblquote": "\u201d", "emspace": " ", "enspace": " ",
    "qmspace": " ", "line": "\n",
}
_SYMBOLS = {"~": " ", "-": "", "_": "-", "{": "{", "}": "}", "\\": "\\", ":": "", "|": ""}
_HEADING_STYLES = ("heading", "baslik", "title", "konu basligi")


class _State:
    __slots__ = ("skip", "uc", "intbl", "style", "outline", "sheet", "entry")

    def __init__(self) -> None:
        self.skip = False
        self.uc = 1
        self.intbl = False
        self.style = 0
        self.outline: int | None = None
        self.sheet = False  # stil sayfası hedefi içinde
        self.entry: list[str] | None = None  # stil sayfası girdisinin metni

    def copy(self) -> _State:
        other = _State()
        for name in self.__slots__:
            setattr(other, name, getattr(self, name))
        return other


class _Parser:
    def __init__(self) -> None:
        self.state = _State()
        self.stack: list[_State] = []
        self.codepage = "cp1254"
        self.styles: dict[int, str] = {}
        self.entry_style: dict[int, int] = {}  # id(entry) → stil numarası
        self.items: list[Item] = []
        self.text: list[str] = []
        self.pending: bytearray = bytearray()
        self.fallback = 0
        self.cells: list[str] = []
        self.rows: list[list[str]] = []

    # -- çıktı -------------------------------------------------------------- #

    def _flush_bytes(self) -> None:
        if self.pending:
            chunk = bytes(self.pending).decode(self.codepage, errors="replace")
            self.pending.clear()
            self._emit(chunk)

    def _emit(self, chunk: str) -> None:
        if self.state.entry is not None:
            self.state.entry.append(chunk)
        elif self.state.sheet:
            return
        else:
            self.text.append(chunk)

    def _take(self) -> str:
        self._flush_bytes()
        raw = "".join(self.text)
        self.text = []
        # \u ile gelen vekil çiftlerini birleştir.
        raw = raw.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
        return squeeze(raw)

    def _flush_table(self) -> None:
        if self.cells:
            self.rows.append(self.cells)
            self.cells = []
        if self.rows:
            self.items.append(Item("table", rows=self.rows))
            self.rows = []

    def _end_paragraph(self) -> None:
        if self.state.intbl:
            self._flush_bytes()
            self.text.append("\n")
            return
        text = self._take()
        self._flush_table()
        if not text:
            return
        style = fold(self.styles.get(self.state.style, ""))
        heading = (style.startswith(_HEADING_STYLES) or self.state.outline is not None
                   or looks_like_heading(text))
        self.items.append(Item("heading" if heading else "paragraph", text))

    def _end_cell(self) -> None:
        self.cells.append(" ".join(self._take().split()))

    def _end_row(self) -> None:
        rest = self._take()
        if rest:
            self.cells.append(" ".join(rest.split()))
        if self.cells:
            self.rows.append(self.cells)
        self.cells = []

    # -- ayrıştırma --------------------------------------------------------- #

    def _control(self, word: str, arg: str | None) -> None:
        state = self.state
        if word in _SKIP_DESTINATIONS:
            state.skip = True
            return
        if word == "stylesheet":
            state.sheet = True
            return
        if state.sheet and state.entry is not None:
            if word == "s" and arg is not None:
                self.entry_style[id(state.entry)] = int(arg)
            elif word in ("cs", "ds", "ts"):  # karakter/bölüm/tablo stili: paragraf stili değil
                self.entry_style[id(state.entry)] = -1
            return
        if word == "ansicpg" and arg:
            candidate = f"cp{int(arg)}"
            try:
                codecs.lookup(candidate)
                self.codepage = candidate
            except LookupError:
                pass
        elif word == "uc" and arg is not None:
            state.uc = max(0, int(arg))
        elif word == "u" and arg is not None:
            self._flush_bytes()
            value = int(arg)
            self._emit(chr(value + 65536 if value < 0 else value))
            self.fallback = state.uc
            return
        elif word in ("par", "sect", "page"):
            self._flush_bytes()
            self._end_paragraph()
        elif word in ("cell", "nestcell"):
            self._end_cell()
        elif word in ("row", "nestrow"):
            self._end_row()
        elif word == "pard":
            state.intbl, state.style, state.outline = False, 0, None
        elif word == "intbl":
            state.intbl = True
        elif word == "itap" and arg is not None:
            state.intbl = int(arg) > 0
        elif word == "s" and arg is not None:
            state.style = int(arg)
        elif word == "outlinelevel" and arg is not None:
            state.outline = int(arg) if 0 <= int(arg) < 9 else None
        elif word in _SPECIAL:
            self._flush_bytes()
            self._emit(_SPECIAL[word])
        self.fallback = 0

    def run(self, text: str) -> list[Item]:
        position, end = 0, len(text)
        while position < end:
            match = _TOKEN.match(text, position)
            if match is None:  # yalnız "\" ile biten dosyada olur
                break
            position = match.end()
            word, arg, hexbyte, symbol, brace, newline, plain = match.groups()
            if brace == "{":
                self._flush_bytes()
                self.stack.append(self.state)
                self.state = self.state.copy()
                if self.state.sheet and self.state.entry is None:
                    self.state.entry = []  # stil sayfasının bir girdisi: {\s1 heading 1;}
                self.fallback = 0
                continue
            if brace == "}":
                self._flush_bytes()
                closing = self.state
                self.state = self.stack.pop() if self.stack else _State()
                if closing.entry is not None and closing.entry is not self.state.entry:
                    name = "".join(closing.entry).strip().rstrip(";").strip()
                    number = self.entry_style.pop(id(closing.entry), 0)
                    if name and number >= 0:
                        self.styles[number] = name
                self.fallback = 0
                continue
            if self.state.skip:
                if word == "bin" and arg:
                    position += max(0, int(arg))
                continue
            if word is not None:
                if word == "bin" and arg:
                    position += max(0, int(arg))
                    continue
                self._control(word, arg)
            elif hexbyte is not None:
                if self.fallback:
                    self.fallback -= 1
                    continue
                self.pending.append(int(hexbyte, 16))
            elif symbol is not None:
                self.fallback = 0
                if symbol == "*":
                    self.state.skip = True
                elif symbol in ("\n", "\r"):
                    self._flush_bytes()
                    self._end_paragraph()
                elif symbol in _SYMBOLS:
                    self._flush_bytes()
                    self._emit(_SYMBOLS[symbol])
            elif plain is not None:
                if self.fallback:
                    skipped = min(self.fallback, len(plain))
                    plain = plain[skipped:]
                    self.fallback -= skipped
                if not plain:
                    continue
                if any(ord(c) > 127 for c in plain):  # kaçışsız 8 bitlik baytlar
                    for char in plain:
                        if ord(char) > 127:
                            self.pending.append(ord(char))
                        else:
                            self._flush_bytes()
                            self._emit(char)
                else:
                    self._flush_bytes()
                    self._emit(plain)
            # newline: RTF'de ham satır sonları anlamsızdır
        self.state = _State()
        self._end_paragraph()
        self._flush_table()
        return self.items


def rtf_items(data: bytes) -> list[Item]:
    text = data.decode("latin-1")
    if not text.lstrip("\ufeff\xef\xbb\xbf \t\r\n").startswith("{\\rtf"):
        raise FormatError("RTF dosyası değil ('{\\rtf' ile başlamıyor)")
    return _Parser().run(text)


def looks_like_rtf(data: bytes) -> bool:
    return data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"{\\rtf")

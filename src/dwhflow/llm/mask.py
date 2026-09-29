"""LLM'e gidecek metinde kişisel veri maskeleme (US-6.8).

Maskelenenler ve yer tutucuları:

========  ===============================================================  ==============
Kural     Ne                                                               Yer tutucu
========  ===============================================================  ==============
EPOSTA    e-posta adresi                                                   ``[EPOSTA]``
IBAN      TR + 24 rakam (boşluklu ya da bitişik)                           ``[IBAN]``
KART      13–19 haneli kart numarası (boşluk/tire gruplu), Luhn geçerli    ``[KART]``
TCKN      11 haneli T.C. kimlik numarası, sağlama haneleri geçerli         ``[TCKN]``
TELEFON   Türkiye telefon numarası (+90 / 0 ile, alan kodu 2xx–5xx, 8xx)   ``[TELEFON]``
========  ===============================================================  ==============

Yanlış maskeleme de zararlıdır: tablo/job adı bozulursa LLM çıktısı kanıt denetiminden
geçemez. Bu yüzden tanımlayıcıya yapışık rakamlar (``SRC_2024…``), tarih ve saatler
(``01:00``, ``29.09.2026``) ve sağlama denetiminden geçmeyen sayılar **maskelenmez**.

Maskeleme ``mask=True`` ile istenir; LLM adresi yerel değilse (dış API) **her zaman**
uygulanır — bkz. ``should_mask``.
"""

from __future__ import annotations

import re
from typing import Any

#: (anahtar, yer tutucu, açıklama) — ayarlar ekranında listelenir. Sıra uygulama sırasıdır:
#: e-posta rakam içerebilir, IBAN'ın rakam grupları kart numarasına benzeyebilir.
RULES: tuple[tuple[str, str, str], ...] = (
    ("EPOSTA", "[EPOSTA]", "E-posta adresi"),
    ("IBAN", "[IBAN]", "TR ile başlayan IBAN (24 rakam, boşluklu ya da bitişik)"),
    ("KART", "[KART]", "Kart numarası (13–19 hane, Luhn sağlaması geçerli)"),
    ("TCKN", "[TCKN]", "T.C. kimlik numarası (11 hane, sağlama haneleri geçerli)"),
    ("TELEFON", "[TELEFON]", "Türkiye telefon numarası (+90 ya da 0 ile başlayan)"),
)

_EMAIL = re.compile(
    r"(?<![\w.%+-])[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}(?![\w-])"
)
_IBAN = re.compile(r"(?<![A-Za-z0-9_])[Tt][Rr] ?\d(?: ?\d){23}(?!\d)")
# Rakam grupları (tek boşluk ya da tireyle ayrılmış). Tanımlayıcıya, tarihe, saate ya da
# ondalık sayıya yapışık gruplar aday değildir.
_DIGIT_RUN = re.compile(r"(?<![\w/:.,-])\d+(?:[ -]\d+)*(?![\w/:])")
_TCKN = re.compile(r"(?<![\w/:.,-])[1-9]\d{10}(?![\w/:])")
_PHONE = re.compile(
    r"(?<![\w+(])\(?(?:\+90|0090|0)\)?[ -]?\(?[2-58]\d{2}\)?[ -]?\d{3}[ -]?\d{2}[ -]?\d{2}(?![\d:/])"
)


def luhn_valid(digits: str) -> bool:
    """Luhn (mod 10) sağlaması — kart numaraları için."""
    if not digits.isdigit():
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def tckn_valid(digits: str) -> bool:
    """T.C. kimlik numarası sağlaması: 11 hane, ilk hane 0 değil, 10. ve 11. hane sağlama."""
    if len(digits) != 11 or not digits.isdigit() or digits[0] == "0":
        return False
    d = [int(c) for c in digits]
    tenth = ((d[0] + d[2] + d[4] + d[6] + d[8]) * 7 - (d[1] + d[3] + d[5] + d[7])) % 10
    eleventh = sum(d[:10]) % 10
    return d[9] == tenth and d[10] == eleventh


def _card_grouping(groups: list[str]) -> bool:
    """Kart numarası yazımına uyan gruplama: bitişik, 4'lü gruplar ya da 4-6-5 (Amex)."""
    if len(groups) == 1:
        return True
    lengths = [len(g) for g in groups]
    if lengths == [4, 6, 5]:
        return True
    return all(n == 4 for n in lengths[:-1]) and 1 <= lengths[-1] <= 4


def _mask_cards(text: str, counts: dict[str, int]) -> str:
    def replace(match: re.Match[str]) -> str:
        parts = re.split(r"([ -])", match.group(0))
        groups, separators = parts[0::2], parts[1::2]
        out: list[str] = []
        index = 0
        while index < len(groups):
            found = None
            for end in range(len(groups) - 1, index - 1, -1):  # önce en uzun pencere
                window = groups[index:end + 1]
                digits = "".join(window)
                if 13 <= len(digits) <= 19 and _card_grouping(window) and luhn_valid(digits):
                    found = end
                    break
            last = index if found is None else found
            out.append(groups[index] if found is None else "[KART]")
            if found is not None:
                counts["KART"] = counts.get("KART", 0) + 1
            if last < len(separators):
                out.append(separators[last])
            index = last + 1
        return "".join(out)

    return _DIGIT_RUN.sub(replace, text)


def mask_text(text: str) -> tuple[str, dict[str, int]]:
    """Metindeki kişisel verileri yer tutucularla değiştirir.

    Döner: (maskelenmiş metin, {kural: adet}) — yalnız en az bir kez uygulanan kurallar.
    """
    counts: dict[str, int] = {}
    if not text:
        return text or "", counts

    def simple(key: str, placeholder: str, valid=None):
        def replace(match: re.Match[str]) -> str:
            if valid is not None and not valid(match.group(0)):
                return match.group(0)
            counts[key] = counts.get(key, 0) + 1
            return placeholder
        return replace

    value = _EMAIL.sub(simple("EPOSTA", "[EPOSTA]"), text)
    value = _IBAN.sub(simple("IBAN", "[IBAN]"), value)
    value = _mask_cards(value, counts)
    value = _TCKN.sub(simple("TCKN", "[TCKN]", tckn_valid), value)
    value = _PHONE.sub(simple("TELEFON", "[TELEFON]"), value)
    return value, counts


def add_counts(total: dict[str, int], counts: dict[str, int]) -> dict[str, int]:
    """Maskeleme sayılarını toplar (yerinde) ve toplamı döner."""
    for key, value in counts.items():
        total[key] = total.get(key, 0) + value
    return total


def should_mask(config: Any, mask: bool | None) -> bool:
    """Maskeleme uygulanacak mı? Dış (yerel olmayan) adreste her zaman evet."""
    return bool(mask) or not bool(getattr(config, "is_local", False))


def describe_counts(counts: dict[str, int]) -> str:
    """``{"TCKN": 2, "IBAN": 1}`` → ``"TCKN: 2, IBAN: 1"`` (kural sırasıyla)."""
    order = [key for key, _, _ in RULES]
    return ", ".join(f"{key}: {counts[key]}" for key in order if counts.get(key))

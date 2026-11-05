"""Manuel okuma/yazma icin veri tipi ve gosterim donusumleri."""
from __future__ import annotations

import struct
from typing import List, Optional, Tuple

from .table import FMT_WORDS, Register, decode_value, encode_value, fmt_value, parse_option_map

TABLE_TYPE = "Tablodaki format"
TYPES = [TABLE_TYPE, "UINT16", "INT16", "UINT32", "INT32", "FLOAT32", "UINT64", "INT64", "ASCII"]
BASES = ["Ondalık", "Hex", "Binary"]
WORD_ORDERS = ["Ayarlardaki", "Big endian (AB CD)", "Little endian (CD AB)"]


def typed_register(r: Register, typ: str) -> Register:
    """Secilen veri tipine gore okuma/yazmada kullanilacak register tanimi."""
    if not typ or typ == TABLE_TYPE:
        return r
    if typ == "ASCII":
        return Register(address=r.address, fmt="ASCII", words=max(1, r.words), name=r.name)
    return Register(address=r.address, fmt=typ, words=FMT_WORDS.get(typ, 1), name=r.name)


def word_order(choice: str, default_big: bool) -> bool:
    if choice.startswith("Big"):
        return True
    if choice.startswith("Little"):
        return False
    return default_big


def _raw_int(words: List[int], big: bool) -> int:
    w = list(words) if big else list(reversed(words))
    v = 0
    for x in w:
        v = (v << 16) | (x & 0xFFFF)
    return v


def type_label(r: Register, typ: str) -> str:
    t = typed_register(r, typ)
    lab = t.fmt
    if t is r and t.fmt.upper().startswith("UINT") and t.signed:
        lab += " (işaretli)"
    return lab + (f" ({t.words} word)" if t.words > 1 else "")


def decode_display(r: Register, words: List[int], typ: str, base: str, big: bool) -> Tuple[str, Optional[float]]:
    """(gosterilecek metin, sayisal deger) — sayisal deger Min/Max kontrolu ve anlam icin."""
    t = typed_register(r, typ)
    if len(words) != t.words:
        return f"(yeniden okuyun: {len(words)} word okundu, {t.fmt} {t.words} word)", None
    if t.fmt == "ASCII":
        w = list(words) if big else list(reversed(words))
        b = b"".join(x.to_bytes(2, "big") for x in w)
        txt = "".join(chr(c) if 32 <= c < 127 else "·" for c in b).rstrip("\x00")
        return txt, None
    v = decode_value(t, words, big)
    if base == "Hex":
        return "0x" + f"{_raw_int(words, big):0{4 * t.words}X}", v
    if base == "Binary":
        s = f"{_raw_int(words, big):0{16 * t.words}b}"
        return " ".join(s[i:i + 4] for i in range(0, len(s), 4)), v
    return fmt_value(v), v


def parse_input(text: str) -> float:
    t = text.strip().replace(" ", "").replace("_", "")
    tl = t.lower()
    if tl.startswith("0x"):
        return int(tl, 16)
    if tl.startswith("0b"):
        return int(tl, 2)
    if tl.endswith("h") and all(c in "0123456789abcdef" for c in tl[:-1]) and tl[:-1]:
        return int(tl[:-1], 16)
    f = float(t.replace(",", "."))
    return int(f) if f.is_integer() else f


def encode_input(r: Register, text: str, typ: str, big: bool) -> Tuple[List[int], Optional[float]]:
    """Kullanicinin girdigi metni secilen tipe gore word listesine cevirir."""
    t = typed_register(r, typ)
    if t.fmt == "ASCII":
        b = text.encode("latin-1", "replace")[: 2 * t.words].ljust(2 * t.words, b"\x00")
        words = [int.from_bytes(b[i:i + 2], "big") for i in range(0, len(b), 2)]
        return (words if big else list(reversed(words))), None
    v = parse_input(text)
    if not t.is_float:
        if isinstance(v, float):
            raise ValueError(f"{t.fmt} için tam sayı girin")
        bits = 16 * t.words
        lo = -(1 << (bits - 1)) if t.fmt.startswith("INT") else 0
        hi = (1 << bits) - 1 if not t.fmt.startswith("INT") else (1 << (bits - 1)) - 1
        if t.fmt.startswith("INT") and v > hi and v < (1 << bits):
            v = v - (1 << bits)        # hex/binary ile girilen isaretli deger
        if not lo <= v <= hi:
            raise ValueError(f"{t.fmt} aralığı {lo}..{hi}")
    else:
        struct.pack(">f", float(v))
    return encode_value(t, v, big), v


def meaning(r: Register, v: Optional[float]) -> str:
    """Remarks icindeki '0: 4800 1: 9600' gibi secenek listesinden degerin anlami."""
    if v is None or isinstance(v, float) and not float(v).is_integer():
        return ""
    opts = parse_option_map(r.remarks)
    return opts.get(int(v), "") if len(opts) >= 2 else ""

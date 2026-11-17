"""Register tablosu modeli ve import (PDF / Excel / CSV)."""
from __future__ import annotations

import csv
import io
import json
import re
import struct
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

# Kolon basliklari icin esanlamlilar (kucuk harf, bosluksuz karsilastirilir)
COLUMN_ALIASES = {
    "address": ["address", "adres", "addr", "registeraddress", "registeradresi", "addressdec", "adresdec"],
    "hex": ["addresshex", "hex", "adreshex", "hexaddress", "hexadres"],
    "fmt": ["format", "type", "datatype", "veritipi", "tip", "tur"],
    "words": ["wordcounts", "wordcount", "words", "word", "count", "registercount", "uzunluk", "wordsayisi"],
    "mcu_byte": ["mcubyte", "byte", "bytes"],
    "name": ["designation", "baslik", "name", "description", "aciklama", "parametre", "parameter", "isim", "tanim", "ad"],
    "unit": ["unit", "birim", "bicim"],
    "access": ["r/w", "rw", "access", "erisim", "okuma/yazma", "o/y", "mode", "r:okumaw:yazma", "r:readw:write"],
    "remarks": ["remarks", "remark", "notes", "note", "not", "notlar", "aciklamalar", "aciklama", "comment"],
    "multiplier": ["multiplier", "carpan", "scale", "olcek"],
    "na_value": ["valueifnotavailable", "navalue", "notavailable", "yokdeger", "gecersizdeger", "bitmaskesi", "bitmask", "mask", "maske"],
    "min": ["min", "minimum", "enaz"],
    "max": ["max", "maximum", "encok"],
}

FMT_WORDS = {"UINT16": 1, "INT16": 1, "UINT32": 2, "INT32": 2, "FLOAT32": 2, "FLOAT": 2, "UINT64": 4, "INT64": 4}

TR_MAP = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")


def _norm_header(h) -> str:
    h = str(h or "").translate(TR_MAP).lower()
    return re.sub(r"[\s_\-\.]+", "", h)


def _to_int(v) -> Optional[int]:
    if v is None:
        return None
    s = str(v).strip().replace(" ", "")
    if not s or s in ("-", "--"):
        return None
    try:
        if s.lower().startswith("0x"):
            return int(s, 16)
        f = float(s.replace(",", "."))
        return int(f) if f.is_integer() else None
    except ValueError:
        return None


def _to_num(v) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip().replace(" ", "")
    if not s or s in ("-", "--"):
        return None
    try:
        if s.lower().startswith("0x"):
            return float(int(s, 16))
        return float(s.replace(",", "."))
    except ValueError:
        return None


def normalize_access(v) -> str:
    s = str(v or "").strip().upper().replace(" ", "")
    s = s.translate(TR_MAP)
    if s in ("R/W", "RW", "R-W", "W/R", "O/Y", "OKUMA/YAZMA", "READ/WRITE", "READWRITE"):
        return "RW"
    if s in ("R", "RO", "O", "READ", "OKUMA", "READONLY"):
        return "R"
    if s in ("W", "WO", "Y", "WRITE", "YAZMA", "WRITEONLY"):
        return "W"
    if "R" in s and "W" in s:
        return "RW"
    if "W" in s:
        return "W"
    if "R" in s:
        return "R"
    return ""


ACCESS_LABEL = {"R": "R", "W": "W", "RW": "R/W", "": "-"}


@dataclass
class Register:
    address: int
    hex: str = ""
    fmt: str = "UINT16"
    words: int = 1
    mcu_byte: str = ""
    name: str = ""
    unit: str = ""
    access: str = "R"        # R, W, RW
    remarks: str = ""
    multiplier: str = ""
    na_value: str = ""
    min: Optional[float] = None
    max: Optional[float] = None
    section: str = ""
    skip: bool = False
    skip_reason: str = ""
    comm_param: str = ""     # address/baud/parity/stopbits/databits (otomatik tespit)
    danger: str = ""         # dolu ise bu registera test amacli ASLA yazilmaz (orn. RS485'i kapatan register)
    needs_password: bool = False   # fabrika ayari: yazmadan once sifre registerlari yazilmali
    password_value: Optional[int] = None  # sifre registeri ise yazilacak sabit deger

    @property
    def readable(self) -> bool:
        return self.access in ("R", "RW")

    @property
    def writable(self) -> bool:
        return self.access in ("W", "RW")

    @property
    def signed(self) -> bool:
        f = self.fmt.upper()
        return f.startswith("INT") or (self.min is not None and self.min < 0)

    @property
    def is_float(self) -> bool:
        return "FLOAT" in self.fmt.upper() or "REAL" in self.fmt.upper()

    def to_dict(self):
        d = asdict(self)
        d["access_label"] = ACCESS_LABEL.get(self.access, self.access)
        return d

    @staticmethod
    def from_dict(d):
        r = Register(address=int(d["address"]))
        for k in Register.__dataclass_fields__:
            if k in d and k != "address":
                setattr(r, k, d[k])
        return r


# --------------------------------------------------------------------------
# Deger kodlama
# --------------------------------------------------------------------------
def encode_value(reg: Register, value: float, word_order_big: bool = True) -> List[int]:
    n = reg.words
    if reg.is_float and n == 2:
        b = struct.pack(">f", float(value))
    else:
        v = int(round(value))
        bits = 16 * n
        if v < 0:
            v = (1 << bits) + v
        v &= (1 << bits) - 1
        b = v.to_bytes(2 * n, "big")
    words = [int.from_bytes(b[i:i + 2], "big") for i in range(0, 2 * n, 2)]
    return words if word_order_big else list(reversed(words))


def decode_value(reg: Register, words: List[int], word_order_big: bool = True) -> float:
    w = list(words) if word_order_big else list(reversed(words))
    b = b"".join(x.to_bytes(2, "big") for x in w)
    if reg.is_float and len(w) == 2:
        return struct.unpack(">f", b)[0]
    v = int.from_bytes(b, "big")
    bits = 16 * len(w)
    if reg.signed and v >= 1 << (bits - 1):
        v -= 1 << bits
    return v


def fmt_value(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and not v.is_integer():
        return f"{v:.6g}"
    return str(int(v))


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------
def _map_columns(header: List) -> Dict[str, int]:
    cols: Dict[str, int] = {}
    normed = [_norm_header(h) for h in header]
    for key, aliases in COLUMN_ALIASES.items():
        for i, h in enumerate(normed):
            if i in cols.values():
                continue
            if h in aliases:
                cols[key] = i
                break
    # "Address" basligi "addresshex"i yakalamasin diye hex once eslenmis olabilir; tekrar kontrol
    return cols


def _looks_like_header(row: List) -> bool:
    cols = _map_columns(row)
    return "address" in cols and ("access" in cols or "name" in cols)


def rows_to_registers(rows: List[List]) -> Tuple[List[Register], List[str]]:
    """Ham tablo satirlarindan register listesi uretir. Baslik satirini kendisi bulur."""
    warnings: List[str] = []
    cols = None
    ncols = 0
    section = ""
    section_access = ""
    has_password = False
    regs: List[Register] = []
    seen = set()
    for row in rows:
        row = [("" if c is None else str(c).replace("\n", " ").strip()) for c in row]
        if not any(row):
            continue
        if _looks_like_header(row):
            cols = _map_columns(row)
            ncols = max(cols.values()) + 1
            continue
        if cols is None:
            continue

        def g(k):
            i = cols.get(k)
            return row[i] if i is not None and i < len(row) else ""

        addr = _to_int(g("address"))
        if addr is None:
            hx = g("hex")
            if hx and re.fullmatch(r"(0x)?[0-9A-Fa-f]{1,4}", hx) and not g("address"):
                addr = int(hx, 16)
        if addr is None:
            # Bolum basligi (ornek: "Settings", "Device Identification")
            # (tablonun saginda kod/aciklama kolonlari olabilir; sadece tablo kolonlarina bakilir)
            texts = [c for c in row[:ncols] if c]
            if len(texts) == 1 and len(texts[0]) < 80:
                section = texts[0]
                section_access = section_access_from_title(section)
            continue
        fmt = (g("fmt") or "UINT16").upper().replace(" ", "")
        if fmt in ("-", ""):
            fmt = "UINT16"
        words = _to_int(g("words")) or FMT_WORDS.get(fmt, 1)
        access = normalize_access(g("access"))
        if not access and "access" not in cols:
            access = section_access   # R/W kolonu yoksa bolum basligindan: "(Read Only)", "(Read - Write)", "(Write only)"
        reg = Register(
            address=addr,
            hex=g("hex") or f"{addr:04X}",
            fmt=fmt,
            words=words,
            mcu_byte=g("mcu_byte"),
            name=g("name"),
            unit=g("unit"),
            access=access,
            remarks=g("remarks"),
            multiplier=g("multiplier"),
            na_value=g("na_value"),
            min=_to_num(g("min")),
            max=_to_num(g("max")),
            section=section,
        )
        if reg.hex and re.fullmatch(r"(0x)?[0-9A-Fa-f]{1,4}", reg.hex) and int(reg.hex, 16) != addr:
            warnings.append(f"{addr}: Hex adres ({reg.hex}) ondalık adresle uyuşmuyor")
        if not access:
            reg.skip = True
            reg.skip_reason = "Erişim tipi (R/W) belirtilmemiş"
            warnings.append(f"{addr}: R/W bilgisi yok (boş/rezerve satır), atlandı")
        if reg.min is not None and reg.max is not None and reg.min > reg.max:
            warnings.append(f"{addr} {reg.name}: Min ({fmt_value(reg.min)}) > Max ({fmt_value(reg.max)}) - tablo hatası, aralık kontrolü yapılmayacak")
            reg.remarks = (reg.remarks + " " if reg.remarks else "") + f"[Tablo: Min={fmt_value(reg.min)} Max={fmt_value(reg.max)}]"
            reg.min = reg.max = None
        mask = _to_int(reg.na_value)
        if mask in (0xFF, 0xFFFF) and reg.max is not None and reg.max > mask and not reg.signed:
            warnings.append(f"{addr} {reg.name}: Max ({fmt_value(reg.max)}) bit maskesini (0x{mask:0{4}X}) aşıyor - "
                            f"test aralığı {mask} ile sınırlandı")
            reg.remarks = (reg.remarks + " " if reg.remarks else "") + f"[Tablo: Max={fmt_value(reg.max)}, maske 0x{mask:04X}]"
            reg.max = float(mask)
            if reg.min is not None and reg.min > reg.max:
                reg.min = None
        d = danger_reason(reg)
        if d:
            reg.danger = d
            warnings.append(f"{addr} {reg.name}: {d} - bu registera test amaçlı yazılmayacak")
        if re.search(r"password|sifre", section.translate(TR_MAP).lower()) and access == "W" \
                and reg.min is not None and reg.min == reg.max:
            reg.password_value = int(reg.min)
            has_password = True
        if addr in seen:
            warnings.append(f"{addr}: tabloda birden fazla kez geçiyor, ilk kayıt kullanıldı")
            continue
        seen.add(addr)
        regs.append(reg)
    if cols is None:
        raise ValueError("Tabloda başlık satırı bulunamadı (en az 'Address' ve 'R/W' veya 'Designation' kolonları gerekli)")
    if has_password:
        pw = [r for r in regs if r.password_value is not None]
        for r in regs:
            if r.password_value is None:
                r.needs_password = True
        warnings.append("Şifre bölümü bulundu (" + ", ".join(f"{r.address}={r.password_value}" for r in pw)
                        + "): bu tablodaki registerlar fabrika ayarı sayılır; yazma testi varsayılan olarak kapalı, "
                          "açılırsa her yazmadan önce şifre yazılır")
    detect_comm_params(regs)
    return regs, warnings


DANGER_PATTERNS = [
    (r"haberlesme\s*kapan", "Yazılırsa haberleşme kapanabilir"),
    (r"fabrika\s*ayarlar\w*\s*yukle|factory\s*(reset|default)", "Fabrika ayarlarını yükleme komutu içeriyor"),
]


def danger_reason(r: Register) -> str:
    text = f"{r.name} {r.remarks}".translate(TR_MAP).lower()
    for pat, why in DANGER_PATTERNS:
        if re.search(pat, text):
            return why
    return ""


def section_access_from_title(title: str) -> str:
    t = title.translate(TR_MAP).lower().replace(" ", "")
    if "read-write" in t or "readwrite" in t or "okuma-yazma" in t or "(r/w)" in t:
        return "RW"
    if "writeonly" in t or "sadeceyazma" in t:
        return "W"
    if "readonly" in t or "saltokunur" in t or "sadeceokuma" in t:
        return "R"
    return ""


def load_pdf(data: bytes) -> List[List]:
    import pdfplumber
    rows: List[List] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            for tb in page.extract_tables():
                # Illustrator PDF'lerinde "ti" ligaturu cozulemeyip U+FFFD olarak gelebiliyor
                rows.extend([[(c.replace("\ufffd", "ti") if isinstance(c, str) else c) for c in r] for r in tb])
    return rows


def load_xlsx(data: bytes) -> List[List]:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    rows: List[List] = []
    for ws in wb.worksheets:
        for r in ws.iter_rows(values_only=True):
            rows.append([("" if c is None else c) for c in r])
    return rows


def load_csv(data: bytes) -> List[List]:
    for enc in ("utf-8-sig", "cp1254", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
        if text.count(";") > text.count(","):
            dialect.delimiter = ";"
    return list(csv.reader(io.StringIO(text), dialect))


def load_table(filename: str, data: bytes) -> Tuple[List[Register], List[str]]:
    fn = filename.lower()
    if fn.endswith(".json"):
        obj = json.loads(data.decode("utf-8"))
        regs = [Register.from_dict(d) for d in obj.get("registers", obj)]
        return regs, []
    if fn.endswith(".pdf"):
        rows = load_pdf(data)
    elif fn.endswith((".xlsx", ".xlsm")):
        rows = load_xlsx(data)
    elif fn.endswith((".csv", ".txt", ".tsv")):
        rows = load_csv(data)
    else:
        raise ValueError("Desteklenen formatlar: PDF, XLSX, CSV, JSON")
    return rows_to_registers(rows)


def save_csv(regs: List[Register]) -> bytes:
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["Section", "Address", "Address Hex", "Format", "Word Counts", "MCU Byte", "Designation", "Unit", "R/W",
                "Remarks", "Multiplier", "Value If Not Available", "Min", "Max"])
    for r in regs:
        w.writerow([r.section, r.address, r.hex, r.fmt, r.words, r.mcu_byte, r.name, r.unit, ACCESS_LABEL[r.access],
                    r.remarks, r.multiplier, r.na_value, fmt_value(r.min), fmt_value(r.max)])
    return out.getvalue().encode("utf-8-sig")


# --------------------------------------------------------------------------
# Haberlesme parametrelerinin tespiti
# --------------------------------------------------------------------------
def parse_option_map(remarks: str) -> Dict[int, str]:
    """'0: 4800 1: 9600 2: 19200' -> {0:'4800',1:'9600',2:'19200'}"""
    out: Dict[int, str] = {}
    parts = re.split(r"(?:(?<=\s)|^)(\d+)\s*[:=\-]\s*", " " + (remarks or "") + " ")
    # parts: ['', '0', '4800 ', '1', '9600 ', ...]
    for i in range(1, len(parts) - 1, 2):
        try:
            k = int(parts[i])
        except ValueError:
            continue
        out[k] = parts[i + 1].strip().rstrip(",;")
    return out


def _parity_from_text(t: str) -> Optional[str]:
    t = t.translate(TR_MAP).lower()
    if "none" in t or "yok" in t or t.strip() == "n":
        return "N"
    if "odd" in t or "tek" in t or t.strip() == "o":
        return "O"
    if "even" in t or "cift" in t or t.strip() == "e":
        return "E"
    return None


def detect_comm_params(regs: List[Register]) -> None:
    for r in regs:
        n = r.name.translate(TR_MAP).upper()
        r.comm_param = ""
        if not r.writable:
            continue
        if "BAUD" in n:
            r.comm_param = "baud"
        elif "PARITY" in n or "PARITE" in n:
            r.comm_param = "parity"
        elif "STOP" in n and "BIT" in n:
            r.comm_param = "stopbits"
        elif ("DATA" in n or "VERI" in n) and "BIT" in n:
            r.comm_param = "databits"
        elif ("MODBUS" in n or "SLAVE" in n) and ("ADDRESS" in n or "ADRES" in n or "ID" in re.split(r"[\s_]+", n)):
            r.comm_param = "address"
    if not any(r.comm_param == "address" for r in regs):
        for r in regs:
            n = r.name.translate(TR_MAP).upper()
            if r.writable and not r.needs_password and ("CIHAZ" in n or "DEVICE" in n) and ("ADDRESS" in n or "ADRES" in n):
                r.comm_param = "address"
                break
    # her tur icin sadece ilk register kullanilir
    seen_kinds = set()
    for r in regs:
        if r.comm_param:
            if r.comm_param in seen_kinds or r.needs_password:
                r.comm_param = ""
            else:
                seen_kinds.add(r.comm_param)


def default_comm_config(regs: List[Register], current_slave: int = 1) -> List[dict]:
    """Ayar tarama modu için varsayilan parametre listesi.

    Her eleman: {kind, register, values:[{raw, setting}], restore}
    """
    out = []
    for r in regs:
        if not r.comm_param:
            continue
        kind = r.comm_param
        vals = []
        opts = parse_option_map(r.remarks)
        if kind == "address":
            lo = int(r.min) if r.min is not None else 1
            hi = int(r.max) if r.max is not None else 247
            cand = sorted({lo, (lo + hi) // 2, hi})
            vals = [{"raw": v, "setting": v} for v in cand]
        elif kind == "baud":
            for k, t in sorted(opts.items()):
                m = re.search(r"\d{3,6}", t.replace(".", ""))
                if m:
                    vals.append({"raw": k, "setting": int(m.group())})
        elif kind == "parity":
            for k, t in sorted(opts.items()):
                p = _parity_from_text(t)
                if p:
                    vals.append({"raw": k, "setting": p})
        elif kind == "stopbits":
            for k, t in sorted(opts.items()):
                m = re.search(r"[12](?:[.,]5)?", t)
                if m:
                    vals.append({"raw": k, "setting": float(m.group().replace(",", "."))})
        elif kind == "databits":
            for k, t in sorted(opts.items()):
                m = re.search(r"[5-8]", t)
                if m:
                    vals.append({"raw": k, "setting": int(m.group())})
        out.append({"kind": kind, "register": r.address, "name": r.name, "values": vals, "enabled": bool(vals)})
    return out

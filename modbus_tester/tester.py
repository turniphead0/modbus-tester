"""Register testleri: okuma, yazma (+geri okuma dogrulamasi), blok okuma, blok yazma."""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

from .jobs import Job, OK, FAIL, WARN, SKIP, NA
from .rtu import ModbusClient, ModbusError, ModbusException, NoResponse, LineSettings
from .table import Register, encode_value, decode_value, fmt_value, _to_int

TEST_KEYS = ["read", "write", "block_read", "block_write", "ro_protect", "range_protect"]
TEST_LABELS = {
    "read": "Okuma",
    "write": "Yazma + Doğrulama",
    "block_read": "Blok Okuma",
    "block_write": "Blok Yazma",
    "ro_protect": "Salt Okunur Koruma",
    "range_protect": "Aralık Disi Koruma",
}


@dataclass
class TestOptions:
    read_fc: int = 3                 # salt okunur registerlar icin 3 veya 4
    single_write: bool = True
    test_boundaries: bool = True     # min ve max degerlerini yaz
    block_read: bool = True
    block_write: bool = True
    block_size: int = 16
    ro_protection: bool = True       # R registera yazilamadigini dogrula (mevcut deger yazilir)
    range_protection: bool = False   # min-1 / max+1 reddedilmeli
    write_only_regs: bool = False    # sadece W registerlara yaz (geri okuma yok)
    restore: bool = True             # testten sonra orijinal degerleri geri yaz
    skip_comm: bool = True           # haberlesme ayar registerlarini yazma testinden cikar
    settle_ms: int = 0               # yazma ile geri okuma arasi bekleme
    word_order_big: bool = True
    address_offset: int = 0          # tablodaki adres - offset = protokol adresi

    @staticmethod
    def from_dict(d: dict) -> "TestOptions":
        o = TestOptions()
        for k, v in (d or {}).items():
            if hasattr(o, k) and v is not None and v != "":
                cur = getattr(o, k)
                if isinstance(cur, bool):
                    v = v in (True, "true", "1", 1, "on", "True")
                elif isinstance(cur, int):
                    v = int(v)
                setattr(o, k, v)
        return o


@dataclass
class RegResult:
    address: int
    read: str = NA
    write: str = NA
    block_read: str = NA
    block_write: str = NA
    ro_protect: str = NA
    range_protect: str = NA
    value: str = ""            # ilk okunan deger
    raw: str = ""              # ham hex
    written: str = ""          # yazilan degerler
    readback: str = ""         # geri okunan
    block_read_group: str = ""
    block_write_group: str = ""
    notes: List[str] = field(default_factory=list)

    @property
    def overall(self) -> str:
        vals = [getattr(self, k) for k in TEST_KEYS]
        if FAIL in vals:
            return FAIL
        if WARN in vals:
            return WARN
        if OK in vals:
            return OK
        if SKIP in vals:
            return SKIP
        return NA

    def to_dict(self):
        d = asdict(self)
        d["overall"] = self.overall
        return d


@dataclass
class TestRun:
    settings: dict
    options: dict
    started: str
    finished: str = ""
    registers: List[dict] = field(default_factory=list)
    results: Dict[int, RegResult] = field(default_factory=dict)
    summary: dict = field(default_factory=dict)
    device_info: str = ""
    aborted: str = ""

    def to_dict(self):
        return {
            "settings": self.settings, "options": self.options, "started": self.started, "finished": self.finished,
            "summary": self.summary, "device_info": self.device_info, "aborted": self.aborted,
            "results": [self.results[r["address"]].to_dict() for r in self.registers if r["address"] in self.results],
        }


def _err_text(e: Exception) -> str:
    return str(e)


def _set(res: RegResult, key: str, status: str):
    """Kotu sonucu iyi sonucla ezme."""
    order = {NA: 0, SKIP: 1, OK: 2, WARN: 3, FAIL: 4}
    if order[status] >= order[getattr(res, key)]:
        setattr(res, key, status)


def make_groups(regs: List[Register], pred, max_words: int) -> List[List[Register]]:
    groups: List[List[Register]] = []
    cur: List[Register] = []
    end = None
    words = 0
    for r in sorted(regs, key=lambda x: x.address):
        if not pred(r):
            if cur:
                groups.append(cur)
            cur, end, words = [], None, 0
            continue
        if cur and r.address == end and words + r.words <= max_words:
            cur.append(r)
            words += r.words
        else:
            if cur:
                groups.append(cur)
            cur, words = [r], r.words
        end = r.address + r.words
    if cur:
        groups.append(cur)
    return groups


class RegisterTester:
    def __init__(self, client: ModbusClient, regs: List[Register], opts: TestOptions, job: Job):
        self.c = client
        self.regs = [r for r in regs]
        self.o = opts
        self.job = job
        self.res: Dict[int, RegResult] = {r.address: RegResult(r.address) for r in regs}
        self.original: Dict[int, List[int]] = {}

    # ---- yardimcilar
    def pa(self, r: Register) -> int:
        return r.address - self.o.address_offset

    def fc_for(self, r: Register) -> int:
        return self.o.read_fc if r.access == "R" else 3

    def read_reg(self, r: Register) -> List[int]:
        return self.c.read_registers(self.pa(r), r.words, function=self.fc_for(r))

    def write_reg(self, r: Register, words: List[int]):
        if r.words == 1:
            self.c.write_register(self.pa(r), words[0])
        else:
            self.c.write_registers(self.pa(r), words)

    def dec(self, r, words):
        return decode_value(r, words, self.o.word_order_big)

    def enc(self, r, v):
        return encode_value(r, v, self.o.word_order_big)

    def settle(self):
        if self.o.settle_ms:
            time.sleep(self.o.settle_ms / 1000.0)

    def is_na(self, r: Register, words: List[int]) -> bool:
        na = _to_int(r.na_value)
        return na is not None and r.words == 1 and words[0] == na

    def write_candidates(self, r: Register, orig_val) -> List:
        if r.min is None or r.max is None:
            return []
        lo, hi = r.min, r.max
        if not r.is_float:
            lo, hi = int(lo), int(hi)
        if self.o.test_boundaries:
            vals = [lo] if lo == hi else [lo, hi]
        else:
            vals = [hi if orig_val != hi else lo]
        return vals

    def block_value(self, r: Register, orig_val):
        if r.min is None or r.max is None:
            return orig_val
        lo, hi = r.min, r.max
        if not r.is_float:
            lo, hi = int(lo), int(hi)
        mid = (lo + hi) / 2 if r.is_float else (lo + hi) // 2
        for cand in (mid, lo, hi):
            if cand != orig_val:
                return cand
        return mid

    def in_write_test(self, r: Register) -> bool:
        if r.skip:
            return False
        if self.o.skip_comm and r.comm_param:
            return False
        return True

    # ---- testler
    def run(self) -> None:
        job = self.job
        active = [r for r in self.regs if not r.skip]
        for r in self.regs:
            if r.skip:
                for k in TEST_KEYS:
                    setattr(self.res[r.address], k, SKIP)
                self.res[r.address].notes.append(r.skip_reason or "Kullanıcı tarafından atlandı")
        steps = [("read", 1.0)]
        if self.o.single_write:
            steps.append(("write", 3.0))
        if self.o.ro_protection:
            steps.append(("ro", 1.0))
        if self.o.range_protection:
            steps.append(("range", 2.0))
        if self.o.block_read:
            steps.append(("bread", 0.3))
        if self.o.block_write:
            steps.append(("bwrite", 0.6))
        total = sum(w for _, w in steps)
        done_w = 0.0

        def prog(i, n, w):
            job.progress = min(0.999, (done_w + w * (i / max(1, n))) / total)

        for name, w in steps:
            getattr(self, "t_" + name)(active, lambda i, n, _w=w: prog(i, n, _w))
            done_w += w
        job.progress = 1.0

    def t_read(self, regs, prog):
        self.job.set_phase("Tekli okuma testi")
        rr = [r for r in regs if r.readable]
        for i, r in enumerate(rr):
            self.job.check()
            prog(i, len(rr))
            res = self.res[r.address]
            try:
                words = self.read_reg(r)
            except ModbusError as e:
                res.read = FAIL
                res.notes.append(f"Okuma: {_err_text(e)}")
                self.job.log(f"{r.address} {r.name}: OKUMA HATA - {e}")
                continue
            self.original[r.address] = words
            v = self.dec(r, words)
            res.value = fmt_value(v)
            res.raw = " ".join(f"0x{w:04X}" for w in words)
            res.read = OK
            if self.is_na(r, words):
                res.notes.append("Okunan değer 'Value If Not Available' ile aynı")
            elif (r.min is not None and v < r.min) or (r.max is not None and v > r.max):
                res.read = WARN
                res.notes.append(f"Okunan değer ({fmt_value(v)}) Min/Max ({fmt_value(r.min)}..{fmt_value(r.max)}) dışında")
            self.job.log(f"{r.address} {r.name}: {res.value} ({res.raw}) {res.read}")
        for r in regs:
            if r.access == "W":
                self.res[r.address].read = NA

    def t_write(self, regs, prog):
        self.job.set_phase("Tekli yazma + geri okuma doğrulama testi")
        wr = [r for r in regs if r.writable]
        for i, r in enumerate(wr):
            self.job.check()
            prog(i, len(wr))
            res = self.res[r.address]
            if not self.in_write_test(r):
                res.write = SKIP
                res.notes.append("Haberleşme ayarı - yazma testi 'Ayar Tarama' modunda yapılır")
                continue
            if r.access == "W":
                if not self.o.write_only_regs:
                    res.write = SKIP
                    res.notes.append("Sadece yazılabilir register - yazma seçeneği kapalı")
                    continue
                v = r.min if r.min is not None else 0
                try:
                    self.write_reg(r, self.enc(r, v))
                    res.write = OK
                    res.written = fmt_value(v)
                    res.notes.append("W register: geri okuma yapılamaz, sadece onay kontrol edildi")
                except ModbusError as e:
                    res.write = FAIL
                    res.notes.append(f"Yazma: {e}")
                continue
            orig = self.original.get(r.address)
            if orig is None:
                try:
                    orig = self.read_reg(r)
                    self.original[r.address] = orig
                except ModbusError as e:
                    res.write = FAIL
                    res.notes.append(f"Yazma öncesi okuma başarısız: {e}")
                    continue
            ov = self.dec(r, orig)
            cands = self.write_candidates(r, ov)
            if not cands:
                cands = [ov]
                res.notes.append("Min/Max yok: mevcut değer geri yazılarak test edildi")
            written, back = [], []
            status = OK
            for v in cands:
                words = self.enc(r, v)
                try:
                    self.write_reg(r, words)
                except ModbusError as e:
                    status = FAIL
                    written.append(f"{fmt_value(v)}!")
                    res.notes.append(f"Yazma {fmt_value(v)}: {e}")
                    continue
                written.append(fmt_value(v))
                self.settle()
                try:
                    rb = self.read_reg(r)
                except ModbusError as e:
                    status = FAIL
                    back.append("?")
                    res.notes.append(f"Geri okuma: {e}")
                    continue
                rv = self.dec(r, rb)
                back.append(fmt_value(rv))
                if rb != words:
                    status = FAIL
                    res.notes.append(f"Doğrulama: {fmt_value(v)} yazıldı, {fmt_value(rv)} okundu")
            if self.o.restore and cands != [ov]:
                try:
                    self.write_reg(r, orig)
                    self.settle()
                    if self.read_reg(r) != orig:
                        status = FAIL
                        res.notes.append("Orijinal değer geri yüklenemedi")
                except ModbusError as e:
                    status = FAIL
                    res.notes.append(f"Orijinal değer geri yazılamadı: {e}")
            res.write = status
            res.written = ", ".join(written)
            res.readback = ", ".join(back)
            self.job.log(f"{r.address} {r.name}: yaz [{res.written}] oku [{res.readback}] {status}")

    def t_ro(self, regs, prog):
        self.job.set_phase("Salt okunur koruma testi (R registerlara yazma reddedilmeli)")
        rr = [r for r in regs if r.access == "R"]
        for i, r in enumerate(rr):
            self.job.check()
            prog(i, len(rr))
            res = self.res[r.address]
            orig = self.original.get(r.address)
            if orig is None:
                res.ro_protect = SKIP
                continue
            try:
                self.write_reg(r, orig)  # mevcut deger -> kabul edilse bile degisiklik olmaz
            except ModbusException as e:
                res.ro_protect = OK
                continue
            except NoResponse:
                res.ro_protect = WARN
                res.notes.append("Salt okunur yazmaya yanıt yok (istisna beklenirdi)")
                continue
            except ModbusError as e:
                res.ro_protect = WARN
                res.notes.append(f"Salt okunur yazma: {e}")
                continue
            res.ro_protect = FAIL
            res.notes.append("Salt okunur register yazmayı kabul etti")
            self.job.log(f"{r.address} {r.name}: salt okunur ama yazma kabul edildi!")

    def t_range(self, regs, prog):
        self.job.set_phase("Aralık dışı değer testi (min-1 / max+1 reddedilmeli)")
        rr = [r for r in regs if r.access == "RW" and self.in_write_test(r) and (r.min is not None or r.max is not None)]
        for i, r in enumerate(rr):
            self.job.check()
            prog(i, len(rr))
            res = self.res[r.address]
            orig = self.original.get(r.address)
            if orig is None:
                res.range_protect = SKIP
                continue
            bits = 16 * r.words
            lo_lim = -(1 << (bits - 1)) if r.signed else 0
            hi_lim = (1 << (bits - 1)) - 1 if r.signed else (1 << bits) - 1
            tests = []
            if r.min is not None and r.min - 1 >= lo_lim:
                tests.append(r.min - 1)
            if r.max is not None and r.max + 1 <= hi_lim:
                tests.append(r.max + 1)
            if not tests:
                res.range_protect = NA
                continue
            status = OK
            for v in tests:
                try:
                    self.write_reg(r, self.enc(r, v))
                except ModbusException:
                    continue
                except ModbusError as e:
                    status = WARN
                    res.notes.append(f"Aralık dışı {fmt_value(v)}: {e}")
                    continue
                try:
                    rb = self.read_reg(r)
                    if rb == self.enc(r, v):
                        status = FAIL
                        res.notes.append(f"Aralık dışı değer {fmt_value(v)} kabul edildi ve saklandi")
                    else:
                        if status == OK:
                            status = WARN
                        res.notes.append(f"Aralık dışı {fmt_value(v)} istisna vermeden kabul edildi (saklanmadı)")
                except ModbusError:
                    pass
                try:
                    self.write_reg(r, orig)
                except ModbusError as e:
                    res.notes.append(f"Orijinal değer geri yazılamadı: {e}")
            res.range_protect = status

    def t_bread(self, regs, prog):
        self.job.set_phase("Blok okuma testi (FC%02d/FC03)" % self.o.read_fc)
        groups = make_groups(regs, lambda r: r.readable and not r.skip, max(1, min(125, self.o.block_size)))
        # okuma fonksiyon kodu ayni olmali (R ve RW farkli FC kullanabilir)
        split = []
        for g in groups:
            cur = []
            for r in g:
                if cur and self.fc_for(cur[-1]) != self.fc_for(r):
                    split.append(cur)
                    cur = []
                cur.append(r)
            if cur:
                split.append(cur)
        for i, g in enumerate(split):
            self.job.check()
            prog(i, len(split))
            label = f"{g[0].address}-{g[-1].address + g[-1].words - 1}"
            n = sum(r.words for r in g)
            if len(g) == 1:
                self.res[g[0].address].block_read = NA
                self.res[g[0].address].notes.append("Blok okuma: bitişik okunabilir register yok")
                continue
            try:
                words = self.c.read_registers(self.pa(g[0]), n, function=self.fc_for(g[0]))
            except ModbusError as e:
                for r in g:
                    _set(self.res[r.address], "block_read", FAIL)
                    self.res[r.address].block_read_group = label
                    self.res[r.address].notes.append(f"Blok okuma {label}: {e}")
                self.job.log(f"Blok okuma {label} ({n} reg): HATA - {e}")
                continue
            off = 0
            mism = 0
            for r in g:
                res = self.res[r.address]
                res.block_read_group = label
                w = words[off:off + r.words]
                off += r.words
                st = OK
                if r.access == "RW" and r.address in self.original and w != self.original[r.address]:
                    st = WARN
                    mism += 1
                    res.notes.append(f"Blok okuma değeri ({fmt_value(self.dec(r, w))}) tekli okumadan farklı ({fmt_value(self.dec(r, self.original[r.address]))})")
                _set(res, "block_read", st)
            self.job.log(f"Blok okuma {label} ({n} reg): OK" + (f", {mism} farklı değer" if mism else ""))

    def t_bwrite(self, regs, prog):
        self.job.set_phase("Blok yazma (FC16) + blok geri okuma testi")
        bs = max(1, min(123, self.o.block_size))
        groups = make_groups(regs, lambda r: r.access == "RW" and self.in_write_test(r), bs)
        for i, g in enumerate(groups):
            self.job.check()
            prog(i, len(groups))
            label = f"{g[0].address}-{g[-1].address + g[-1].words - 1}"
            n = sum(r.words for r in g)
            if len(g) == 1:
                self.res[g[0].address].block_write = NA
                continue
            for r in g:
                self.res[r.address].block_write_group = label
            try:
                orig = self.c.read_registers(self.pa(g[0]), n, function=3)
            except ModbusError as e:
                for r in g:
                    _set(self.res[r.address], "block_write", FAIL)
                    self.res[r.address].notes.append(f"Blok yazma öncesi okuma: {e}")
                continue
            new = []
            off = 0
            for r in g:
                ov = self.dec(r, orig[off:off + r.words])
                new += self.enc(r, self.block_value(r, ov))
                off += r.words
            try:
                self.c.write_registers(self.pa(g[0]), new)
            except ModbusError as e:
                for r in g:
                    _set(self.res[r.address], "block_write", FAIL)
                    self.res[r.address].notes.append(f"Blok yazma {label}: {e}")
                self.job.log(f"Blok yazma {label} ({n} reg): HATA - {e}")
                continue
            self.settle()
            try:
                back = self.c.read_registers(self.pa(g[0]), n, function=3)
            except ModbusError as e:
                back = None
                for r in g:
                    _set(self.res[r.address], "block_write", FAIL)
                    self.res[r.address].notes.append(f"Blok yazma geri okuma: {e}")
            if back is not None:
                off = 0
                bad = 0
                for r in g:
                    res = self.res[r.address]
                    exp = new[off:off + r.words]
                    got = back[off:off + r.words]
                    off += r.words
                    if exp == got:
                        _set(res, "block_write", OK)
                    else:
                        bad += 1
                        _set(res, "block_write", FAIL)
                        res.notes.append(f"Blok yazma doğrulama: {fmt_value(self.dec(r, exp))} yazıldı, {fmt_value(self.dec(r, got))} okundu")
                self.job.log(f"Blok yazma {label} ({n} reg): " + ("OK" if not bad else f"{bad} register doğrulanamadı"))
            if self.o.restore:
                try:
                    self.c.write_registers(self.pa(g[0]), orig)
                    if self.c.read_registers(self.pa(g[0]), n, function=3) != orig:
                        raise ModbusError("geri okuma farklı")
                except ModbusError as e:
                    for r in g:
                        _set(self.res[r.address], "block_write", FAIL)
                        self.res[r.address].notes.append(f"Blok orijinal değerler geri yüklenemedi: {e}")


def summarize(results: Dict[int, RegResult]) -> dict:
    s = {k: {OK: 0, FAIL: 0, WARN: 0, SKIP: 0} for k in TEST_KEYS + ["overall"]}
    for r in results.values():
        for k in TEST_KEYS:
            v = getattr(r, k)
            if v in s[k]:
                s[k][v] += 1
        if r.overall in s["overall"]:
            s["overall"][r.overall] += 1
    s["total"] = len(results)
    return s


def run_register_test(job: Job, regs: List[Register], settings: LineSettings, opts: TestOptions, sim_device=None) -> TestRun:
    run = TestRun(settings=settings.to_dict(), options=asdict(opts), started=time.strftime("%Y-%m-%d %H:%M:%S"),
                  registers=[r.to_dict() for r in regs])
    job.result = run
    job.log(f"Bağlantı: {settings.describe()}  ({len(regs)} register)")
    client = ModbusClient(settings, sim_device=sim_device)
    client.open()
    try:
        # baglanti kontrolu
        first = next((r for r in regs if r.readable and not r.skip), None)
        if first is not None:
            try:
                client.read_registers(first.address - opts.address_offset, first.words,
                                      function=opts.read_fc if first.access == "R" else 3, retries=2)
            except NoResponse:
                run.aborted = "Cihaz yanıt vermiyor - adres/baud/parity ayarlarını kontrol edin"
                job.log("HATA: " + run.aborted)
                raise
            except ModbusError as e:
                job.log(f"İlk okuma hatası (devam ediliyor): {e}")
        t = RegisterTester(client, regs, opts, job)
        run.results = t.res
        try:
            t.run()
        finally:
            run.summary = summarize(t.res)
            run.finished = time.strftime("%Y-%m-%d %H:%M:%S")
            run.summary["stats"] = dict(client.stats)
    finally:
        client.close()
    s = run.summary["overall"]
    job.log(f"Bitti: {s[OK]} OK, {s[WARN]} UYARI, {s[FAIL]} HATA, {s[SKIP]} ATLANDI")
    return run

"""Haberlesme ayarlari tarama modu.

Cihazin Modbus ayar parametrelerini (adres, baud, parity, stop bit, data bit) sirayla
degistirir, her degisiklikten sonra yeni ayarla baglanip cihazin calistigini dogrular,
sonunda orijinal ayarlara geri doner.
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

from .jobs import Job, OK, FAIL, WARN, SKIP, NA
from .rtu import LineSettings, ModbusClient, ModbusError, ModbusException, NoResponse
from .table import Register

KIND_LABEL = {"address": "Modbus Adres", "baud": "Baud Rate", "parity": "Parity", "stopbits": "Stop Bit", "databits": "Data Bit"}
PARITY_LABEL = {"N": "None", "E": "Even", "O": "Odd"}


@dataclass
class ScanOptions:
    mode: str = "sequential"         # sequential | matrix
    settle_ms: int = 1500            # ayar yazildiktan sonra bekleme
    verify_retries: int = 3
    functional_check: bool = True    # yeni ayarla blok okuma + yazma/geri okuma
    functional_regs: int = 10
    check_old_silent: bool = False   # eski ayarla cihaz artik yanit vermemeli
    recovery_search: bool = True
    restore: bool = True
    parity_none_2stop: bool = False  # Modbus standardi: parity yoksa 2 stop bit
    apply_register: str = ""         # ornek "17130=1" (ayari uygulamak icin yazilacak register)
    address_offset: int = 0
    read_fc: int = 3

    @staticmethod
    def from_dict(d):
        o = ScanOptions()
        for k, v in (d or {}).items():
            if hasattr(o, k) and v is not None:
                cur = getattr(o, k)
                if isinstance(cur, bool):
                    v = v in (True, "true", "1", 1, "on", "True")
                elif isinstance(cur, int):
                    v = int(v)
                setattr(o, k, v)
        return o


@dataclass
class ScanStep:
    no: int
    param: str
    register: int
    raw: int
    setting: str
    from_settings: str
    to_settings: str
    purpose: str = "test"           # test | geri donus
    write_resp: str = ""
    verify: str = NA
    old_silent: str = NA
    functional: str = NA
    status: str = NA
    duration: float = 0.0
    note: str = ""


@dataclass
class ScanRun:
    start_settings: dict
    options: dict
    params: List[dict]
    started: str
    finished: str = ""
    steps: List[ScanStep] = field(default_factory=list)
    original: Dict[str, int] = field(default_factory=dict)
    final_settings: str = ""
    aborted: str = ""
    summary: dict = field(default_factory=dict)

    def to_dict(self):
        d = {k: getattr(self, k) for k in ("start_settings", "options", "params", "started", "finished",
                                            "original", "final_settings", "aborted", "summary")}
        d["steps"] = [asdict(s) for s in self.steps]
        return d


def apply_setting(ls: LineSettings, kind: str, setting, opts: ScanOptions) -> LineSettings:
    if kind == "address":
        return ls.copy(slave=int(setting))
    if kind == "baud":
        return ls.copy(baudrate=int(setting))
    if kind == "parity":
        p = str(setting).upper()[:1]
        n = ls.copy(parity=p)
        if opts.parity_none_2stop:
            n.stopbits = 2 if p == "N" else 1
        return n
    if kind == "stopbits":
        return ls.copy(stopbits=float(setting))
    if kind == "databits":
        return ls.copy(bytesize=int(setting))
    raise ValueError(kind)


def current_setting(ls: LineSettings, kind: str):
    return {"address": ls.slave, "baud": ls.baudrate, "parity": ls.parity,
            "stopbits": ls.stopbits, "databits": ls.bytesize}[kind]


def setting_label(kind, setting) -> str:
    if kind == "parity":
        return PARITY_LABEL.get(str(setting), str(setting))
    if kind == "stopbits":
        f = float(setting)
        return str(int(f)) if f.is_integer() else str(f)
    return str(setting)


class CommScanner:
    def __init__(self, job: Job, regs: List[Register], start: LineSettings, params: List[dict], opts: ScanOptions,
                 sim_device=None):
        self.job = job
        self.regs = regs
        self.start = start
        self.params = [p for p in params if p.get("enabled", True) and p.get("values")]
        self.o = opts
        self.client = ModbusClient(start, sim_device=sim_device)
        self.cur = start.copy()
        self.run = ScanRun(start_settings=start.to_dict(), options=asdict(opts), params=params,
                           started=time.strftime("%Y-%m-%d %H:%M:%S"))
        self.func_regs = [r for r in regs if r.readable and not r.skip][: max(1, opts.functional_regs)]
        self.func_rw = next((r for r in regs if r.access == "RW" and not r.comm_param and not r.skip
                             and r.min is not None and r.max is not None and r.words == 1), None)

    # ---- yardimcilar
    def pa(self, addr: int) -> int:
        return addr - self.o.address_offset

    def connect(self, ls: LineSettings):
        self.client.reconfigure(ls)
        self.cur = ls

    def probe(self, ls: LineSettings, reg: int, retries: int = 0) -> Optional[int]:
        self.client.reconfigure(ls)
        try:
            return self.client.read_registers(self.pa(reg), 1, retries=retries)[0]
        except ModbusError:
            return None

    def functional(self) -> (str, str):
        notes = []
        st = OK
        # bitisik olanlari blok halinde oku
        try:
            groups = []
            for r in self.func_regs:
                if groups and r.address == groups[-1][-1].address + groups[-1][-1].words and (r.access == "R") == (groups[-1][-1].access == "R"):
                    groups[-1].append(r)
                else:
                    groups.append([r])
            for g in groups:
                n = sum(r.words for r in g)
                fc = self.o.read_fc if g[0].access == "R" else 3
                self.client.read_registers(self.pa(g[0].address), n, function=fc)
        except ModbusError as e:
            st = FAIL
            notes.append(f"okuma: {e}")
        if self.func_rw is not None and st == OK:
            r = self.func_rw
            try:
                # Mevcut deger geri yazilir: haberlesmeyi (FC06 + geri okuma) dogrular, cihazdaki ayari degistirmez
                # ve tarih gibi birbirine bagli registerlarda gecersiz deger reddi olusmaz.
                orig = self.client.read_registers(self.pa(r.address), 1)[0]
                self.client.write_register(self.pa(r.address), orig)
                back = self.client.read_registers(self.pa(r.address), 1)[0]
                if back != orig:
                    st = FAIL
                    notes.append(f"{r.address} yaz/oku doğrulanamadı ({orig} yazıldı, {back} okundu)")
            except ModbusError as e:
                st = FAIL
                notes.append(f"yazma ({r.address} {r.name}): {e}")
        return st, "; ".join(notes)

    def recovery(self, target: LineSettings, prev: LineSettings, check_reg: int) -> Optional[LineSettings]:
        self.job.log("Kurtarma araması: cihaz bilinen ayar kombinasyonlarında aranıyor...")
        sets: Dict[str, list] = {}
        for p in self.params:
            sets[p["kind"]] = [v["setting"] for v in p["values"]]
        for kind, ls in (("address", target), ("baud", target), ("parity", target)):
            sets.setdefault(kind, [])
        sets["address"] = list(dict.fromkeys([target.slave, prev.slave, self.start.slave] + sets["address"]))
        sets["baud"] = list(dict.fromkeys([target.baudrate, prev.baudrate, self.start.baudrate] + sets["baud"]))
        sets["parity"] = list(dict.fromkeys([target.parity, prev.parity, self.start.parity] + sets["parity"]))
        kinds = list(sets.keys())
        for combo in itertools.product(*[sets[k] for k in kinds]):
            self.job.check()
            ls = prev.copy()
            for k, v in zip(kinds, combo):
                ls = apply_setting(ls, k, v, self.o)
            if self.probe(ls, check_reg, retries=0) is not None:
                self.job.log(f"Cihaz bulundu: {ls.describe()}")
                return ls
        return None

    # ---- tek adim
    def step(self, p: dict, val: dict, purpose: str = "test") -> bool:
        """True: devam edilebilir, False: cihaz kayboldu."""
        self.job.check()
        kind = p["kind"]
        reg = int(p["register"])
        prev = self.cur.copy()
        target = apply_setting(prev, kind, val["setting"], self.o)
        st = ScanStep(no=len(self.run.steps) + 1, param=KIND_LABEL.get(kind, kind), register=reg, raw=int(val["raw"]),
                      setting=setting_label(kind, val["setting"]), from_settings=prev.short(), to_settings=target.short(),
                      purpose=purpose)
        self.run.steps.append(st)
        t0 = time.time()
        self.job.log(f"Adım {st.no}: {st.param} -> {st.setting} (reg {reg}={st.raw})  {prev.short()} => {target.short()}")
        self.client.reconfigure(prev)
        try:
            self.client.write_register(self.pa(reg), int(val["raw"]) & 0xFFFF)
            st.write_resp = "Onaylandı"
        except ModbusException as e:
            st.write_resp = f"Reddedildi: {e}"
            st.status = FAIL
            st.note = "Cihaz tablodaki geçerli bir değeri reddetti"
            st.duration = round(time.time() - t0, 2)
            self.job.log(f"  {st.write_resp}")
            return True
        except ModbusError as e:
            st.write_resp = f"Yanıt yok ({e})"
        if self.o.apply_register.strip():
            try:
                a, _, v = self.o.apply_register.partition("=")
                self.client.write_register(self.pa(int(a)), int(v or 1))
            except (ModbusError, ValueError) as e:
                st.note += f"Uygulama registerı: {e}. "
        time.sleep(self.o.settle_ms / 1000.0)

        # dogrulama
        self.connect(target)
        expect = int(val["raw"]) & 0xFFFF
        got = None
        for _ in range(max(1, self.o.verify_retries)):
            self.job.check()
            got = self.probe(target, reg, retries=0)
            if got is not None:
                break
            time.sleep(0.3)
        if got is None:
            st.verify = FAIL
            # eski ayarla hala calisiyor mu?
            old = self.probe(prev, reg, retries=1)
            if old is not None:
                st.note += f"Cihaz yeni ayarla yanıt vermedi, eski ayarda ({prev.short()}) çalışmaya devam ediyor (reg={old}). "
                self.connect(prev)
                st.status = FAIL
            else:
                found = self.recovery(target, prev, reg) if self.o.recovery_search else None
                if found is None:
                    st.status = FAIL
                    st.note += "Cihaz hiçbir bilinen ayarda bulunamadı. "
                    st.duration = round(time.time() - t0, 2)
                    self.job.log("  HATA: cihaz kayboldu")
                    return False
                st.note += f"Cihaz beklenmeyen ayarda bulundu: {found.short()}. "
                self.connect(found)
                st.status = FAIL
            st.duration = round(time.time() - t0, 2)
            self.job.log(f"  {st.status}: {st.note}")
            return True
        if got != expect:
            st.verify = WARN
            st.note += f"Yeni ayarla yanıt var fakat register değeri {got} (beklenen {expect}). "
        else:
            st.verify = OK
        if self.o.check_old_silent and prev != target:
            r = self.probe(prev, reg, retries=0)
            st.old_silent = OK if r is None else WARN
            if r is not None:
                st.note += ("Eski ayarla da yanıt veriyor (cihaz parity bitini kontrol etmiyor olabilir). " if kind == "parity"
                            else "Eski ayarla da yanıt veriyor. ")
            self.client.reconfigure(target)
        if self.o.functional_check:
            self.client.reconfigure(target)
            st.functional, n = self.functional()
            if n:
                st.note += n
        vals = [st.verify, st.old_silent, st.functional]
        st.status = FAIL if FAIL in vals else WARN if WARN in vals else OK
        st.duration = round(time.time() - t0, 2)
        self.job.log(f"  doğrulama={st.verify} fonksiyon={st.functional} -> {st.status}")
        return True

    def value_for(self, p: dict, setting) -> Optional[dict]:
        for v in p["values"]:
            if v["setting"] == setting:
                return v
        return None

    def original_value(self, p: dict) -> Optional[dict]:
        raw = self.run.original.get(p["kind"])
        for v in p["values"]:
            if v["raw"] == raw:
                return v
        if raw is not None and p["kind"] == "address":
            return {"raw": raw, "setting": raw}
        return None

    # ---- ana akis
    def execute(self) -> ScanRun:
        job = self.job
        run = self.run
        job.set_phase("Ayar tarama: başlangıç kontrolu")
        self.client.open()
        try:
            for p in self.params:
                v = self.probe(self.start, int(p["register"]), retries=2)
                if v is None:
                    run.aborted = f"Başlangıç ayariyla ({self.start.describe()}) cihaz yanıt vermiyor"
                    job.log("HATA: " + run.aborted)
                    return run
                run.original[p["kind"]] = v
                ov = self.original_value(p)
                cur = current_setting(self.start, p["kind"])
                job.log(f"{KIND_LABEL[p['kind']]}: register {p['register']} = {v}" + (f" ({setting_label(p['kind'], ov['setting'])})" if ov else ""))
                if ov is not None and ov["setting"] != cur and p["kind"] != "stopbits":
                    job.log(f"  UYARI: register değeri bağlantı ayarı ({cur}) ile uyuşmuyor")
            self.connect(self.start)

            plan: List[tuple] = []
            if self.o.mode == "matrix":
                combos = list(itertools.product(*[p["values"] for p in self.params]))
                for combo in combos:
                    plan.append(("combo", combo))
            else:
                for p in self.params:
                    ov = self.original_value(p)
                    for v in p["values"]:
                        if ov is not None and v["raw"] == ov["raw"]:
                            continue
                        plan.append(("one", p, v))
                    if ov is not None:
                        plan.append(("restore", p, ov))
            total = max(1, len(plan))
            job.set_phase(f"Ayar tarama ({'tüm kombinasyonlar' if self.o.mode == 'matrix' else 'sıralı'}): {total} adım")
            for i, item in enumerate(plan):
                job.progress = i / total * 0.95
                if item[0] == "combo":
                    for p, v in zip(self.params, item[1]):
                        if current_setting(self.cur, p["kind"]) != v["setting"]:
                            if not self.step(p, v):
                                run.aborted = "Cihazla bağlantı kayboldu"
                                return run
                else:
                    p, v = item[1], item[2]
                    if item[0] == "restore" and current_setting(self.cur, p["kind"]) == v["setting"]:
                        continue
                    if not self.step(p, v, "geri dönüş" if item[0] == "restore" else "test"):
                        run.aborted = "Cihazla bağlantı kayboldu"
                        return run
            if self.o.restore:
                job.set_phase("Orijinal ayarlara geri dönüş")
                # once adres disindakiler, en son adres
                for p in sorted(self.params, key=lambda p: p["kind"] == "address"):
                    ov = self.original_value(p)
                    if ov is not None and current_setting(self.cur, p["kind"]) != ov["setting"]:
                        if not self.step(p, ov, "geri dönüş"):
                            run.aborted = "Geri dönüş sırasında cihaz kayboldu"
                            return run
            return run
        finally:
            run.final_settings = self.cur.describe()
            run.finished = time.strftime("%Y-%m-%d %H:%M:%S")
            tests = [s for s in run.steps]
            run.summary = {
                "steps": len(tests),
                OK: sum(1 for s in tests if s.status == OK),
                WARN: sum(1 for s in tests if s.status == WARN),
                FAIL: sum(1 for s in tests if s.status == FAIL),
            }
            job.progress = 1.0
            self.client.close()
            job.log(f"Ayar tarama bitti: {run.summary[OK]} OK, {run.summary[WARN]} UYARI, {run.summary[FAIL]} HATA. Son ayar: {run.final_settings}")


def run_comm_scan(job: Job, regs, start: LineSettings, params, opts: ScanOptions, sim_device=None) -> ScanRun:
    sc = CommScanner(job, regs, start, params, opts, sim_device)
    job.result = sc.run
    return sc.execute()

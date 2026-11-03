"""Register tablosundan sanal Modbus cihazi (simulator).

- Uygulama icinde 'SIM' portu ile kullanilir (gercek cihaz olmadan deneme).
- `python -m modbus_tester.simulator --table tablo.pdf --port COM5` ile gercek /
  sanal seri portta (com0com vb.) slave olarak da calistirilabilir.
"""
from __future__ import annotations

import argparse
import random
import struct
import threading
import time
from typing import Dict, List, Optional, Set

from .rtu import LineSettings, Transport, NoResponse, crc16, add_crc
from .table import Register, default_comm_config, encode_value


class SimDevice:
    def __init__(self, regs: List[Register], line: LineSettings, faults: Optional[dict] = None):
        self.regs: Dict[int, Register] = {}
        self.mem: Dict[int, int] = {}
        self.owner: Dict[int, Register] = {}
        rnd = random.Random(42)
        for r in regs:
            self.regs[r.address] = r
            if r.min is not None and r.max is not None:
                v = r.min if r.min >= r.max else rnd.randint(int(r.min), int(r.max)) if not r.is_float else (r.min + r.max) / 2
            elif r.min is not None:
                v = r.min
            else:
                v = rnd.randint(0, 100)
            for i, w in enumerate(encode_value(r, v)):
                self.mem[r.address + i] = w
                self.owner[r.address + i] = r
        self.line = line.copy()
        self.comm = {c["kind"]: c for c in default_comm_config(regs)}
        # Haberlesme registerlarini mevcut hat ayarina esitle
        for kind, c in self.comm.items():
            cur = {"address": line.slave, "baud": line.baudrate, "parity": line.parity,
                   "stopbits": line.stopbits, "databits": line.bytesize}[kind]
            for v in c["values"]:
                if v["setting"] == cur:
                    self.mem[c["register"]] = v["raw"]
            if kind == "address":
                self.mem[c["register"]] = line.slave
        self.faults = faults or {}
        self.no_store: Set[int] = set(self.faults.get("no_store", []))      # yazmayi onaylar ama saklamaz
        self.accept_ro: Set[int] = set(self.faults.get("accept_ro", []))   # salt okunuru yazdirir
        self.no_range: bool = bool(self.faults.get("no_range", False))     # aralik kontrolu yapmaz
        self.no_block_write: bool = bool(self.faults.get("no_block_write", False))
        self.reject_values: Dict[str, list] = self.faults.get("reject_comm", {})  # {"baud":[0]}
        self.pending: Optional[LineSettings] = None
        self.lock = threading.Lock()
        self.log: List[str] = []

    # --- yardimcilar
    def _exc(self, fc, code):
        return bytes([fc | 0x80, code])

    def _check_write(self, addr: int, words: List[int]) -> Optional[int]:
        for i in range(len(words)):
            if addr + i not in self.owner:
                return 2
        r = self.regs.get(addr)
        if r is None:
            return 2
        if not r.writable and addr not in self.accept_ro:
            return 2
        validator = self.faults.get("validator")
        if validator is not None:
            code = validator(addr, words[0], self.mem)
            if code:
                return code
        if not self.no_range and (r.min is not None or r.max is not None):
            from .table import decode_value
            v = decode_value(r, words[: r.words])
            if (r.min is not None and v < r.min) or (r.max is not None and v > r.max):
                return 3
        if r.comm_param:
            c = self.comm.get(r.comm_param)
            if r.comm_param in self.reject_values and words[0] in self.reject_values[r.comm_param]:
                return 3
            if c and r.comm_param != "address" and c["values"] and words[0] not in [v["raw"] for v in c["values"]]:
                return 3
        return None

    def _apply_comm(self, reg: Register, raw: int):
        new = (self.pending or self.line).copy()
        c = self.comm.get(reg.comm_param)
        setting = raw
        if c and reg.comm_param != "address":
            for v in c["values"]:
                if v["raw"] == raw:
                    setting = v["setting"]
        if reg.comm_param == "address":
            new.slave = raw
        elif reg.comm_param == "baud":
            new.baudrate = int(setting)
        elif reg.comm_param == "parity":
            new.parity = str(setting)
        elif reg.comm_param == "stopbits":
            new.stopbits = float(setting)
        elif reg.comm_param == "databits":
            new.bytesize = int(setting)
        self.pending = new

    def _store(self, addr, words):
        i = 0
        while i < len(words):
            r = self.owner[addr + i]
            if r.address not in self.no_store:
                for j in range(r.words):
                    if 0 <= i + j < len(words):
                        self.mem[r.address + j] = words[i + j]
                if r.comm_param:
                    self._apply_comm(r, words[i])
            i += r.words

    def handle(self, pdu: bytes) -> bytes:
        fc = pdu[0]
        if fc in (3, 4):
            addr, n = struct.unpack(">HH", pdu[1:5])
            if not 1 <= n <= 125:
                return self._exc(fc, 3)
            vals = []
            for a in range(addr, addr + n):
                if a not in self.mem:
                    return self._exc(fc, 2)
                vals.append(self.mem[a])
            # saat registerlarinda zamanla degisen deger (gercekci olsun)
            return bytes([fc, 2 * n]) + struct.pack(f">{n}H", *vals)
        if fc == 6:
            addr, val = struct.unpack(">HH", pdu[1:5])
            err = self._check_write(addr, [val])
            if err:
                return self._exc(fc, err)
            self._store(addr, [val])
            return pdu[:5]
        if fc == 16:
            if self.no_block_write:
                return self._exc(fc, 1)
            addr, n, bc = struct.unpack(">HHB", pdu[1:6])
            vals = list(struct.unpack(f">{n}H", pdu[6:6 + 2 * n]))
            a = addr
            i = 0
            while i < n:
                r = self.owner.get(a + i)
                if r is None or r.address != a + i:
                    return self._exc(fc, 2)
                err = self._check_write(r.address, vals[i:i + r.words])
                if err:
                    return self._exc(fc, err)
                i += r.words
            self._store(addr, vals)
            return pdu[:5]
        return self._exc(fc, 1)

    def line_matches(self, s: LineSettings) -> bool:
        L = self.line
        return (s.baudrate == L.baudrate and s.parity == L.parity and int(s.bytesize) == int(L.bytesize))

    def process(self, slave: int, pdu: bytes, line: LineSettings) -> Optional[bytes]:
        with self.lock:
            if not self.line_matches(line):
                return None  # cerceve bozuk -> cihaz susar
            if slave not in (0, self.line.slave):
                return None
            resp = self.handle(pdu)
            if self.pending is not None:
                self.log.append(f"Haberleşme ayarı degisti: {self.line.short()} -> {self.pending.short()}")
                self.line = self.pending
                self.pending = None
            return None if slave == 0 else resp


class SimTransport(Transport):
    def __init__(self, settings: LineSettings, device: SimDevice):
        super().__init__(settings)
        if device is None:
            raise ValueError("Simulator için once tablo yükleyin")
        self.device = device

    def reconfigure(self, settings):
        self.settings = settings

    def transact(self, slave, pdu, expect_len=None):
        time.sleep(0.001)
        resp = self.device.process(slave, pdu, self.settings)
        if resp is None:
            if slave == 0:
                return b""
            time.sleep(min(self.settings.timeout, 0.05))
            raise NoResponse()
        return resp


# --------------------------------------------------------------------------
# Seri port uzerinde slave olarak calistirma
# --------------------------------------------------------------------------
def serve_serial(device: SimDevice, port: str, stop: Optional[threading.Event] = None, verbose=False):
    import serial
    ser = serial.Serial(port, device.line.baudrate, bytesize=device.line.bytesize,
                        parity=device.line.parity, stopbits=int(device.line.stopbits), timeout=0.01)
    buf = bytearray()
    last = time.monotonic()
    applied = device.line.copy()
    while stop is None or not stop.is_set():
        chunk = ser.read(256)
        now = time.monotonic()
        if chunk:
            buf += chunk
            last = now
            continue
        if buf and now - last > 0.01:
            frame = bytes(buf)
            buf.clear()
            if len(frame) < 4 or crc16(frame[:-2]) != struct.unpack("<H", frame[-2:])[0]:
                if verbose:
                    print("Bozuk çerçeve", frame.hex(" "))
                continue
            resp = device.process(frame[0], frame[1:-2], device.line)
            if verbose:
                print("RX", frame.hex(" "), "TX", resp.hex(" ") if resp else "-")
            if resp is not None:
                ser.write(add_crc(bytes([frame[0]]) + resp))
                ser.flush()
            if device.line != applied:
                time.sleep(0.01)
                ser.baudrate = device.line.baudrate
                ser.parity = device.line.parity
                ser.bytesize = device.line.bytesize
                applied = device.line.copy()
                print("Yeni hat ayarı:", device.line.describe())


def main():
    from .table import load_table
    ap = argparse.ArgumentParser(description="Tablodan Modbus RTU slave simulatoru")
    ap.add_argument("--table", required=True)
    ap.add_argument("--port", required=True)
    ap.add_argument("--slave", type=int, default=1)
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--parity", default="N")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    regs, _ = load_table(a.table, open(a.table, "rb").read())
    dev = SimDevice(regs, LineSettings(slave=a.slave, baudrate=a.baud, parity=a.parity))
    print(f"Simulator {a.port} uzerinde: {dev.line.describe()} ({len(regs)} register)")
    serve_serial(dev, a.port, verbose=a.v)


if __name__ == "__main__":
    main()

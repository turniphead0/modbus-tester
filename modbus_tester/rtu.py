"""Modbus RTU / TCP master (istemci) katmani.

Harici Modbus kutuphanesine bagimli degildir; sadece pyserial kullanir.
Desteklenen fonksiyon kodlari: 03, 04, 06, 16.
"""
from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass, replace, asdict
from typing import Callable, List, Optional

try:
    import serial  # pyserial
except ImportError:  # pragma: no cover
    serial = None


EXCEPTION_TEXT = {
    1: "Geçersiz fonksiyon (01)",
    2: "Geçersiz veri adresi (02)",
    3: "Geçersiz veri değeri (03)",
    4: "Cihaz hatası (04)",
    5: "Onaylandı, işleniyor (05)",
    6: "Cihaz meşgul (06)",
    8: "Hafıza parite hatası (08)",
    10: "Gateway yolu yok (0A)",
    11: "Gateway hedef yanıt vermedi (0B)",
}


class ModbusError(Exception):
    """Tum Modbus hatalarinin tabani."""


class NoResponse(ModbusError):
    def __init__(self, msg: str = "Yanıt yok (zaman aşımı)"):
        super().__init__(msg)


class FrameError(ModbusError):
    pass


class ModbusException(ModbusError):
    def __init__(self, function: int, code: int):
        self.function = function
        self.code = code
        super().__init__(f"Modbus istisna: {EXCEPTION_TEXT.get(code, f'kod {code}')} (FC{function:02d})")


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def add_crc(frame: bytes) -> bytes:
    return frame + struct.pack("<H", crc16(frame))


PARITY_NAMES = {"N": "None", "E": "Even", "O": "Odd", "M": "Mark", "S": "Space"}


@dataclass
class LineSettings:
    """Haberleşme ayarları."""
    transport: str = "rtu"          # rtu | tcp | rtu_over_tcp | sim
    port: str = ""                 # COM3, /dev/ttyUSB0, veya host:port (tcp)
    slave: int = 1
    baudrate: int = 9600
    parity: str = "N"               # N E O
    bytesize: int = 8
    stopbits: float = 1
    timeout: float = 0.5            # saniye
    retries: int = 1
    inter_request_delay: float = 0.02  # saniye

    def describe(self) -> str:
        if self.transport in ("tcp", "rtu_over_tcp"):
            return f"{self.transport.upper()} {self.port} adres={self.slave}"
        sb = int(self.stopbits) if float(self.stopbits).is_integer() else self.stopbits
        return f"adres={self.slave} {self.baudrate} {self.bytesize}{self.parity}{sb}"

    def short(self) -> str:
        sb = int(self.stopbits) if float(self.stopbits).is_integer() else self.stopbits
        return f"{self.baudrate}-{self.bytesize}{self.parity}{sb} @{self.slave}"

    def copy(self, **kw) -> "LineSettings":
        return replace(self, **kw)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "LineSettings":
        ls = LineSettings()
        for k, v in (d or {}).items():
            if hasattr(ls, k) and v is not None and v != "":
                cur = getattr(ls, k)
                if isinstance(cur, bool):
                    v = bool(v)
                elif isinstance(cur, int) and not isinstance(cur, bool):
                    v = int(float(v))
                elif isinstance(cur, float):
                    v = float(v)
                setattr(ls, k, v)
        ls.parity = str(ls.parity).upper()[:1] or "N"
        return ls


# --------------------------------------------------------------------------
# Transport katmani: PDU gonder, PDU al
# --------------------------------------------------------------------------
class Transport:
    def __init__(self, settings: LineSettings):
        self.settings = settings

    def open(self):
        pass

    def close(self):
        pass

    def reconfigure(self, settings: LineSettings):
        """Haberleşme ayarlarını degistir (port acik kalabilir)."""
        self.close()
        self.settings = settings
        self.open()

    def transact(self, slave: int, pdu: bytes, expect_len: Callable[[bytes], Optional[int]]) -> bytes:
        raise NotImplementedError


def _expected_pdu_len(pdu_head: bytes) -> Optional[int]:
    """Yanıt PDU'sunun toplam uzunlugunu dondurur (ilk 2 byte'a gore)."""
    if len(pdu_head) < 1:
        return None
    fc = pdu_head[0]
    if fc & 0x80:
        return 2
    if fc in (1, 2, 3, 4, 0x17):
        if len(pdu_head) < 2:
            return None
        return 2 + pdu_head[1]
    if fc in (5, 6, 15, 16):
        return 5
    return None


class SerialRTUTransport(Transport):
    def __init__(self, settings: LineSettings):
        super().__init__(settings)
        self.ser = None

    def open(self):
        if serial is None:
            raise ModbusError("pyserial kurulu degil: pip install pyserial")
        s = self.settings
        stop = {1: serial.STOPBITS_ONE, 1.5: serial.STOPBITS_ONE_POINT_FIVE, 2: serial.STOPBITS_TWO}[float(s.stopbits) if float(s.stopbits) == 1.5 else int(s.stopbits)]
        if self.ser is None:
            self.ser = serial.Serial()
            self.ser.port = s.port
        self.ser.apply_settings({"baudrate": int(s.baudrate), "bytesize": int(s.bytesize), "parity": s.parity,
                                 "stopbits": stop, "timeout": 0.005})
        if not self.ser.is_open:
            self.ser.open()

    def close(self):
        if self.ser is not None and self.ser.is_open:
            self.ser.close()
        self.ser = None

    def reconfigure(self, settings: LineSettings):
        port_changed = self.ser is None or settings.port != self.settings.port
        self.settings = settings
        if port_changed:
            self.close()
        self.open()  # acik porta yeni ayarlar uygulanir

    def _char_time(self) -> float:
        s = self.settings
        bits = 1 + int(s.bytesize) + (0 if s.parity == "N" else 1) + float(s.stopbits)
        return bits / float(s.baudrate)

    def transact(self, slave, pdu, expect_len=_expected_pdu_len):
        s = self.settings
        frame = add_crc(bytes([slave]) + pdu)
        t35 = max(3.5 * self._char_time(), 0.00175)
        self.ser.reset_input_buffer()
        time.sleep(t35)
        self.ser.write(frame)
        self.ser.flush()
        if slave == 0:  # broadcast: yanit beklenmez
            time.sleep(0.1)
            return b""
        buf = bytearray()
        deadline = time.monotonic() + s.timeout + len(frame) * self._char_time()
        last_rx = None
        gap = max(t35 * 2, 0.02)
        need = None
        while True:
            chunk = self.ser.read(256)
            now = time.monotonic()
            if chunk:
                buf += chunk
                last_rx = now
                if need is None and len(buf) >= 3:
                    n = expect_len(bytes(buf[1:3]))
                    if n is not None:
                        need = 1 + n + 2
                if need is not None and len(buf) >= need:
                    break
            else:
                if last_rx is not None and now - last_rx > gap and len(buf) >= 5:
                    break  # cerceve sonu (sessizlik)
                if now > deadline:
                    break
        if not buf:
            raise NoResponse()
        frame_in = bytes(buf[:need]) if need else bytes(buf)
        if len(frame_in) < 4:
            raise FrameError(f"Eksik çerçeve: {frame_in.hex(' ')}")
        if crc16(frame_in[:-2]) != struct.unpack("<H", frame_in[-2:])[0]:
            raise FrameError(f"CRC hatası: {frame_in.hex(' ')}")
        if frame_in[0] != slave:
            raise FrameError(f"Yanıt farklı adresten geldi ({frame_in[0]})")
        return frame_in[1:-2]


class TCPTransport(Transport):
    """Modbus TCP (MBAP) veya RTU-over-TCP."""

    def __init__(self, settings: LineSettings):
        super().__init__(settings)
        self.sock = None
        self.tid = 0

    def open(self):
        host, _, port = self.settings.port.partition(":")
        self.sock = socket.create_connection((host, int(port or 502)), timeout=self.settings.timeout)
        self.sock.settimeout(self.settings.timeout)

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def _recv(self, n):
        data = b""
        while len(data) < n:
            try:
                chunk = self.sock.recv(n - len(data))
            except socket.timeout:
                raise NoResponse()
            if not chunk:
                raise NoResponse("Bağlantı kapandı")
            data += chunk
        return data

    def transact(self, slave, pdu, expect_len=_expected_pdu_len):
        if self.sock is None:
            self.open()
        if self.settings.transport == "rtu_over_tcp":
            self.sock.sendall(add_crc(bytes([slave]) + pdu))
            head = self._recv(3)
            n = expect_len(head[1:3])
            rest = self._recv(1 + n + 2 - 3)
            frame = head + rest
            if crc16(frame[:-2]) != struct.unpack("<H", frame[-2:])[0]:
                raise FrameError("CRC hatası")
            return frame[1:-2]
        self.tid = (self.tid + 1) & 0xFFFF
        self.sock.sendall(struct.pack(">HHHB", self.tid, 0, len(pdu) + 1, slave) + pdu)
        head = self._recv(7)
        tid, _, length, _ = struct.unpack(">HHHB", head)
        body = self._recv(length - 1)
        return body


def make_transport(settings: LineSettings, sim_device=None) -> Transport:
    if settings.transport == "sim":
        from .simulator import SimTransport
        return SimTransport(settings, sim_device)
    if settings.transport in ("tcp", "rtu_over_tcp"):
        return TCPTransport(settings)
    return SerialRTUTransport(settings)


# --------------------------------------------------------------------------
# Istemci
# --------------------------------------------------------------------------
class ModbusClient:
    def __init__(self, settings: LineSettings, sim_device=None, trace: Optional[Callable[[str], None]] = None):
        self.settings = settings
        self.sim_device = sim_device
        self.transport = make_transport(settings, sim_device)
        self.trace = trace
        self.lock = threading.Lock()
        self.stats = {"requests": 0, "timeouts": 0, "errors": 0}

    def open(self):
        self.transport.open()
        return self

    def close(self):
        self.transport.close()

    def reconfigure(self, settings: LineSettings):
        self.settings = settings
        self.transport.reconfigure(settings)

    def __enter__(self):
        return self.open()

    def __exit__(self, *a):
        self.close()

    def _request(self, pdu: bytes, retries: Optional[int] = None, slave: Optional[int] = None) -> bytes:
        s = self.settings
        slave = s.slave if slave is None else slave
        tries = 1 + (s.retries if retries is None else retries)
        last = None
        with self.lock:
            for _ in range(tries):
                self.stats["requests"] += 1
                try:
                    if s.inter_request_delay:
                        time.sleep(s.inter_request_delay)
                    if self.trace:
                        self.trace(f"TX [{slave}] {pdu.hex(' ')}")
                    resp = self.transport.transact(slave, pdu, _expected_pdu_len)
                    if self.trace:
                        self.trace(f"RX [{slave}] {resp.hex(' ')}")
                    if slave == 0:
                        return b""
                    if not resp:
                        raise FrameError("Boş yanıt")
                    if resp[0] == (pdu[0] | 0x80):
                        raise ModbusException(pdu[0], resp[1])  # istisna -> tekrar denenmez
                    if resp[0] != pdu[0]:
                        raise FrameError(f"Beklenmeyen fonksiyon kodu {resp[0]}")
                    return resp
                except ModbusException:
                    raise
                except NoResponse as e:
                    self.stats["timeouts"] += 1
                    last = e
                except FrameError as e:
                    self.stats["errors"] += 1
                    last = e
        raise last

    # --- fonksiyonlar
    def read_registers(self, address: int, count: int, function: int = 3, **kw) -> List[int]:
        if not 1 <= count <= 125:
            raise ValueError("count 1..125 olmalı")
        resp = self._request(struct.pack(">BHH", function, address, count), **kw)
        if resp[1] != count * 2 or len(resp) != 2 + count * 2:
            raise FrameError(f"Beklenen {count} register, gelen {resp[1] // 2}")
        return list(struct.unpack(f">{count}H", resp[2:]))

    def write_register(self, address: int, value: int, **kw) -> None:
        resp = self._request(struct.pack(">BHH", 6, address, value & 0xFFFF), **kw)
        if kw.get("slave") == 0:
            return
        if resp[1:5] != struct.pack(">HH", address, value & 0xFFFF):
            raise FrameError("FC06 yankısı istekle uyuşmuyor")

    def write_registers(self, address: int, values: List[int], **kw) -> None:
        n = len(values)
        if not 1 <= n <= 123:
            raise ValueError("register sayısı 1..123 olmalı")
        pdu = struct.pack(">BHHB", 16, address, n, n * 2) + struct.pack(f">{n}H", *[v & 0xFFFF for v in values])
        resp = self._request(pdu, **kw)
        if kw.get("slave") == 0:
            return
        if resp[1:5] != struct.pack(">HH", address, n):
            raise FrameError("FC16 yanıtı istekle uyuşmuyor")

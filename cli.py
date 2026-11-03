"""Komut satiri arayuzu (otomasyon / tekrarli testler icin).

Ornekler:
  python cli.py test --table tablo.pdf --port COM3 --slave 1 --baud 9600 --parity N
  python cli.py scan --table tablo.pdf --port COM3 --slave 1 --baud 9600
  python cli.py test --table tablo.pdf --sim            (cihazsiz deneme)
  python cli.py export --table tablo.pdf --out tablo.csv
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modbus_tester.table import load_table, save_csv, default_comm_config  # noqa: E402
from modbus_tester.rtu import LineSettings  # noqa: E402
from modbus_tester.simulator import SimDevice  # noqa: E402
from modbus_tester.tester import TestOptions, run_register_test  # noqa: E402
from modbus_tester.commscan import ScanOptions, run_comm_scan  # noqa: E402
from modbus_tester.jobs import Job  # noqa: E402
from modbus_tester.report import write_reports  # noqa: E402


def conn_args(p):
    p.add_argument("--table", required=True, help="Register tablosu (PDF/XLSX/CSV)")
    p.add_argument("--port", default="", help="COM3, /dev/ttyUSB0 veya TCP icin host:port")
    p.add_argument("--transport", default="rtu", choices=["rtu", "tcp", "rtu_over_tcp"])
    p.add_argument("--sim", action="store_true", help="Gercek cihaz yerine simulator kullan")
    p.add_argument("--slave", type=int, default=1)
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--parity", default="N", choices=["N", "E", "O"])
    p.add_argument("--databits", type=int, default=8, choices=[7, 8])
    p.add_argument("--stopbits", type=float, default=1, choices=[1, 1.5, 2])
    p.add_argument("--timeout", type=int, default=500, help="ms")
    p.add_argument("--retries", type=int, default=1)
    p.add_argument("--delay", type=int, default=20, help="istekler arasi bekleme, ms")
    p.add_argument("--skip", default="", help="Atlanacak adresler: 6000,6001,7000-7100")
    p.add_argument("--offset", type=int, default=0, help="adres ofseti")
    p.add_argument("--out", default="raporlar", help="rapor klasoru")
    p.add_argument("--title", default="")


def parse_skip(s):
    out = set()
    for part in filter(None, [x.strip() for x in s.split(",")]):
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def follow(job: Job):
    n = 0
    while not job.done:
        time.sleep(0.2)
        for line in job.logs[n:]:
            print(line)
        n = len(job.logs)
    for line in job.logs[n:]:
        print(line)


def main():
    ap = argparse.ArgumentParser(description="Modbus cihaz test araci (komut satiri)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("test", help="Register testleri")
    conn_args(t)
    t.add_argument("--fc", type=int, default=3, choices=[3, 4], help="R registerlar icin okuma fonksiyonu")
    t.add_argument("--block-size", type=int, default=16)
    t.add_argument("--no-write", action="store_true")
    t.add_argument("--no-block-read", action="store_true")
    t.add_argument("--no-block-write", action="store_true")
    t.add_argument("--no-ro-check", action="store_true")
    t.add_argument("--range-check", action="store_true")
    t.add_argument("--no-restore", action="store_true")
    s = sub.add_parser("scan", help="Haberlesme ayarlari tarama modu")
    conn_args(s)
    s.add_argument("--matrix", action="store_true", help="tum kombinasyonlar")
    s.add_argument("--settle", type=int, default=1500, help="ayar sonrasi bekleme, ms")
    s.add_argument("--addresses", default="", help="denenecek slave adresleri: 1,2,247")
    s.add_argument("--only", default="", help="sadece bu parametreler: address,baud,parity")
    e = sub.add_parser("export", help="Tabloyu CSV'ye aktar")
    e.add_argument("--table", required=True)
    e.add_argument("--out", required=True)
    a = ap.parse_args()

    regs, warns = load_table(a.table, open(a.table, "rb").read())
    for w in warns:
        print("Tablo uyarisi:", w)
    if a.cmd == "export":
        open(a.out, "wb").write(save_csv(regs))
        print(f"{len(regs)} register -> {a.out}")
        return
    skip = parse_skip(a.skip)
    for r in regs:
        if r.address in skip:
            r.skip, r.skip_reason = True, "Komut satirinda atlandi"
    ls = LineSettings(transport="sim" if a.sim else a.transport, port="SIM" if a.sim else a.port, slave=a.slave,
                      baudrate=a.baud, parity=a.parity, bytesize=a.databits, stopbits=a.stopbits,
                      timeout=a.timeout / 1000, retries=a.retries, inter_request_delay=a.delay / 1000)
    if not a.sim and not a.port:
        ap.error("--port gerekli (veya --sim)")
    sim = SimDevice(regs, ls) if a.sim else None
    job = Job(a.cmd)
    if a.cmd == "test":
        o = TestOptions(read_fc=a.fc, block_size=a.block_size, single_write=not a.no_write, block_read=not a.no_block_read,
                        block_write=not a.no_block_write, ro_protection=not a.no_ro_check, range_protection=a.range_check,
                        restore=not a.no_restore, address_offset=a.offset)
        job.start(lambda j: run_register_test(j, regs, ls, o, sim))
        follow(job)
        if job.result is not None and job.result.results:
            p = write_reports("test", job.result.to_dict(), a.out, f"register_test_{job.id}", [r.to_dict() for r in regs], a.title)
            print("Rapor:", p["xlsx"], p["html"])
        res = job.result
        sys.exit(1 if job.error or (res and res.summary.get("overall", {}).get("HATA")) else 0)
    else:
        params = default_comm_config(regs)
        if a.only:
            keep = set(a.only.split(","))
            params = [p for p in params if p["kind"] in keep]
        if a.addresses:
            for p in params:
                if p["kind"] == "address":
                    p["values"] = [{"raw": int(x), "setting": int(x)} for x in a.addresses.split(",")]
        for p in params:
            print(f"Parametre {p['kind']}: register {p['register']} degerler {[(v['raw'], v['setting']) for v in p['values']]}")
        o = ScanOptions(mode="matrix" if a.matrix else "sequential", settle_ms=a.settle, address_offset=a.offset)
        job.start(lambda j: run_comm_scan(j, regs, ls, params, o, sim))
        follow(job)
        if job.result is not None:
            p = write_reports("scan", job.result.to_dict(), a.out, f"ayar_tarama_{job.id}", None, a.title)
            print("Rapor:", p["xlsx"], p["html"])
        res = job.result
        sys.exit(1 if job.error or res.aborted or res.summary.get("HATA") else 0)


if __name__ == "__main__":
    main()

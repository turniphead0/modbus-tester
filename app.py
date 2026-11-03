"""Modbus Cihaz Test Araci - yerel web arayuzu.

Calistirma:  python app.py   (tarayici otomatik acilir: http://127.0.0.1:8765)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import webbrowser

from flask import Flask, jsonify, request, send_file, abort, Response

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modbus_tester.table import load_table, save_csv, default_comm_config, Register, detect_comm_params  # noqa: E402
from modbus_tester.rtu import LineSettings, ModbusClient, ModbusError  # noqa: E402
from modbus_tester.simulator import SimDevice  # noqa: E402
from modbus_tester.tester import TestOptions, run_register_test  # noqa: E402
from modbus_tester.commscan import ScanOptions, run_comm_scan  # noqa: E402
from modbus_tester.jobs import Job  # noqa: E402
from modbus_tester.report import write_reports  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(BASE, "raporlar")

app = Flask(__name__, static_folder=os.path.join(BASE, "static"), static_url_path="/static")

STATE = {
    "regs": [],          # List[Register]
    "table_name": "",
    "warnings": [],
    "sim": None,         # SimDevice
    "sim_key": None,
    "job": None,         # Job
    "jobs": {},          # id -> Job
}
LOCK = threading.Lock()


def get_sim(settings: LineSettings, faults: bool):
    key = (id(STATE["regs"]), faults)
    if STATE["sim"] is None or STATE["sim_key"] != key:
        f = None
        if faults:
            ro = next((r.address for r in STATE["regs"] if r.access == "R"), None)
            rw = [r.address for r in STATE["regs"] if r.access == "RW" and not r.comm_param]
            f = {"no_store": rw[1:2], "accept_ro": [ro] if ro is not None else []}
        STATE["sim"] = SimDevice(STATE["regs"], settings, f)
        STATE["sim_key"] = key
    return STATE["sim"]


@app.get("/")
def index():
    return send_file(os.path.join(BASE, "static", "index.html"))


@app.get("/api/ports")
def ports():
    out = []
    try:
        from serial.tools import list_ports
        for p in list_ports.comports():
            out.append({"device": p.device, "desc": p.description})
    except Exception as e:  # pragma: no cover
        return jsonify({"ports": [], "error": str(e)})
    return jsonify({"ports": out})


def table_payload():
    regs = STATE["regs"]
    return {
        "name": STATE["table_name"],
        "count": len(regs),
        "warnings": STATE["warnings"],
        "registers": [r.to_dict() for r in regs],
        "comm": default_comm_config(regs),
        "stats": {
            "R": sum(1 for r in regs if r.access == "R"),
            "RW": sum(1 for r in regs if r.access == "RW"),
            "W": sum(1 for r in regs if r.access == "W"),
            "skip": sum(1 for r in regs if r.skip),
        },
    }


@app.post("/api/table")
def upload_table():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "Dosya seçilmedi"}), 400
    try:
        regs, warnings = load_table(f.filename, f.read())
    except Exception as e:
        return jsonify({"error": f"Tablo okunamadı: {e}"}), 400
    if not regs:
        return jsonify({"error": "Tabloda register bulunamadı"}), 400
    with LOCK:
        STATE["regs"] = regs
        STATE["table_name"] = f.filename
        STATE["warnings"] = warnings
        STATE["sim"] = None
    return jsonify(table_payload())


@app.get("/api/table")
def get_table():
    return jsonify(table_payload())


@app.post("/api/table/skip")
def set_skip():
    data = request.get_json(force=True)
    skips = set(int(a) for a in data.get("skip", []))
    for r in STATE["regs"]:
        if r.access:  # R/W bilgisi olmayanlar her zaman atlanir
            r.skip = r.address in skips
            r.skip_reason = "Kullanıcı tarafından atlandı" if r.skip else ""
    return jsonify({"ok": True, "skip": len(skips)})


@app.get("/api/table/export.csv")
def export_csv():
    return Response(save_csv(STATE["regs"]), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=register_tablosu.csv"})


def settings_from(data) -> LineSettings:
    s = LineSettings.from_dict(data.get("conn", {}))
    s.timeout = float(data.get("conn", {}).get("timeout_ms", 500)) / 1000.0
    s.inter_request_delay = float(data.get("conn", {}).get("delay_ms", 20)) / 1000.0
    return s


@app.post("/api/probe")
def probe():
    data = request.get_json(force=True)
    s = settings_from(data)
    addr = data.get("address")
    sim = get_sim(s, bool(data.get("sim_faults"))) if s.transport == "sim" else None
    if addr in (None, ""):
        r = next((r for r in STATE["regs"] if r.readable), None)
        if r is None:
            return jsonify({"error": "Önce tablo yükleyin veya adres girin"}), 400
        addr = r.address
    addr = int(addr)
    try:
        c = ModbusClient(s, sim_device=sim).open()
        try:
            vals = c.read_registers(addr - int(data.get("address_offset", 0) or 0), int(data.get("count", 1) or 1),
                                    function=int(data.get("fc", 3)))
        finally:
            c.close()
    except ModbusError as e:
        return jsonify({"ok": False, "message": f"{s.describe()}: {e}"})
    except Exception as e:
        return jsonify({"ok": False, "message": f"Port açılamadı: {e}"})
    return jsonify({"ok": True, "message": f"{s.describe()}: register {addr} = {vals}"})


def busy():
    j = STATE["job"]
    return j is not None and not j.done


@app.post("/api/test/start")
def start_test():
    if busy():
        return jsonify({"error": "Zaten çalışan bir is var"}), 409
    if not STATE["regs"]:
        return jsonify({"error": "Önce register tablosu yükleyin"}), 400
    data = request.get_json(force=True)
    s = settings_from(data)
    opts = TestOptions.from_dict(data.get("options", {}))
    sim = get_sim(s, bool(data.get("sim_faults"))) if s.transport == "sim" else None
    regs = STATE["regs"]
    title = data.get("title") or f"Modbus Register Test Raporu - {STATE['table_name']}"
    job = Job("test")

    def work(j: Job):
        run = None
        try:
            run = run_register_test(j, regs, s, opts, sim)
        finally:
            r = j.result
            if r is not None and r.results:
                j.reports = write_reports("test", r.to_dict(), REPORT_DIR, f"register_test_{j.id}",
                                          [x.to_dict() for x in regs], title)
                j.log(f"Rapor kaydedildi: {j.reports['xlsx']}")
    STATE["job"] = job
    STATE["jobs"][job.id] = job
    job.start(work)
    return jsonify({"job": job.id})


@app.post("/api/scan/start")
def start_scan():
    if busy():
        return jsonify({"error": "Zaten çalışan bir is var"}), 409
    data = request.get_json(force=True)
    s = settings_from(data)
    opts = ScanOptions.from_dict(data.get("options", {}))
    params = data.get("params") or default_comm_config(STATE["regs"])
    params = [p for p in params if p.get("enabled", True)]
    if not params:
        return jsonify({"error": "Taranacak haberleşme parametresi yok"}), 400
    sim = get_sim(s, bool(data.get("sim_faults"))) if s.transport == "sim" else None
    regs = STATE["regs"]
    title = data.get("title") or f"Modbus Haberleşme Ayarları Tarama Raporu - {STATE['table_name']}"
    job = Job("scan")

    def work(j: Job):
        try:
            run_comm_scan(j, regs, s, params, opts, sim)
        finally:
            if j.result is not None:
                j.reports = write_reports("scan", j.result.to_dict(), REPORT_DIR, f"ayar_tarama_{j.id}", None, title)
                j.log(f"Rapor kaydedildi: {j.reports['xlsx']}")
    STATE["job"] = job
    STATE["jobs"][job.id] = job
    job.start(work)
    return jsonify({"job": job.id})


@app.post("/api/stop")
def stop():
    j = STATE["job"]
    if j:
        j.cancel()
    return jsonify({"ok": True})


@app.get("/api/status")
def status():
    j = STATE["job"]
    if j is None:
        return jsonify({"job": None})
    since = int(request.args.get("since", 0))
    res = j.result
    summary = None
    if res is not None:
        summary = getattr(res, "summary", None)
    return jsonify({
        "job": j.id, "kind": j.kind, "done": j.done, "error": j.error, "progress": j.progress, "phase": j.phase,
        "log": j.logs[since:], "log_len": len(j.logs), "summary": summary,
        "aborted": getattr(res, "aborted", "") if res is not None else "",
        "reports": {k: f"/api/report/{j.id}/{k}" for k in j.reports},
        "final_settings": getattr(res, "final_settings", None),
    })


@app.get("/api/report/<jid>/<ext>")
def report(jid, ext):
    j = STATE["jobs"].get(jid)
    if not j or ext not in j.reports:
        abort(404)
    return send_file(j.reports[ext], as_attachment=ext != "html")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    url = f"http://{a.host}:{a.port}"
    print(f"Modbus Test Araci çalışıyor: {url}  (kapatmak için Ctrl+C)")
    if not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=a.host, port=a.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()

"""Rapor uretimi: Excel (xlsx), HTML (yazdirilabilir / PDF'e kaydedilebilir), CSV, JSON."""
from __future__ import annotations

import csv
import html
import io
import json
import os
from typing import Dict, List

from .jobs import OK, FAIL, WARN, SKIP, NA
from .table import ACCESS_LABEL, fmt_value

SRC_COLS = [
    ("address", "Address"), ("hex", "Address Hex"), ("fmt", "Format"), ("words", "Word Counts"),
    ("mcu_byte", "MCU Byte"), ("name", "Designation"), ("unit", "Unit"), ("access_label", "R/W"),
    ("remarks", "Remarks"), ("multiplier", "Multiplier"), ("na_value", "Value If Not Available"),
    ("min", "Min"), ("max", "Max"),
]
RES_COLS = [
    ("value", "Okunan Değer"), ("raw", "Ham (Hex)"), ("read", "Okuma"),
    ("written", "Yazılan Değer(ler)"), ("readback", "Geri Okunan"), ("write", "Yazma + Doğrulama"),
    ("block_read_group", "Blok Okuma Grubu"), ("block_read", "Blok Okuma"),
    ("block_write_group", "Blok Yazma Grubu"), ("block_write", "Blok Yazma"),
    ("ro_protect", "Salt Okunur Koruma"), ("range_protect", "Aralık Dışı Koruma"),
    ("overall", "SONUÇ"), ("notes", "Açıklama"),
]
STATUS_KEYS = {"read", "write", "block_read", "block_write", "ro_protect", "range_protect", "overall"}
COLORS = {OK: "C6EFCE", FAIL: "FFC7CE", WARN: "FFEB9C", SKIP: "E7E6E6"}
FONT_COLORS = {OK: "006100", FAIL: "9C0006", WARN: "7F6000", SKIP: "595959"}
TEST_NAMES = {"read": "Okuma", "write": "Yazma + Doğrulama", "block_read": "Blok Okuma", "block_write": "Blok Yazma",
              "ro_protect": "Salt Okunur Koruma", "range_protect": "Aralık Dışı Koruma", "overall": "Genel"}
PARITY = {"N": "None", "E": "Even", "O": "Odd"}


def _settings_text(s: dict) -> List[tuple]:
    if s.get("transport") in ("tcp", "rtu_over_tcp"):
        return [("Bağlantı", s["transport"].upper()), ("Host:Port", s["port"]), ("Slave Adresi", s["slave"]),
                ("Zaman Aşımı", f"{s['timeout']} sn"), ("Tekrar", s["retries"])]
    sb = s["stopbits"]
    sb = int(sb) if float(sb).is_integer() else sb
    return [("Bağlantı", "Simülatör" if s.get("transport") == "sim" else "Modbus RTU"), ("Port", s["port"]),
            ("Slave Adresi", s["slave"]), ("Baud Rate", s["baudrate"]), ("Parity", PARITY.get(s["parity"], s["parity"])),
            ("Data Bit", s["bytesize"]), ("Stop Bit", sb), ("Zaman Aşımı", f"{s['timeout']} sn"), ("Tekrar", s["retries"])]


def _row(reg: dict, res: dict) -> Dict[str, str]:
    d = {}
    for k, _ in SRC_COLS:
        v = reg.get(k, "")
        d[k] = fmt_value(v) if k in ("min", "max") else ("" if v is None else str(v))
    for k, _ in RES_COLS:
        v = res.get(k, "") if res else ""
        d[k] = " | ".join(v) if isinstance(v, list) else str(v)
    return d


def _rows_with_sections(run: dict, regs: List[dict]):
    resmap = {r["address"]: r for r in run["results"]}
    section = None
    for reg in regs:
        if reg.get("section") and reg["section"] != section:
            section = reg["section"]
            yield ("section", section)
        yield ("row", _row(reg, resmap.get(reg["address"], {})))


# --------------------------------------------------------------------------
# Register testi raporu
# --------------------------------------------------------------------------
def register_report_xlsx(run: dict, regs: List[dict], path: str, title: str = ""):
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Register Test Raporu"
    thin = Side(style="thin", color="999999")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill("solid", fgColor="1F3864")
    res_fill = PatternFill("solid", fgColor="2E75B6")
    sec_fill = PatternFill("solid", fgColor="D9E1F2")
    cols = SRC_COLS + RES_COLS
    ncol = len(cols)

    ws.cell(1, 1, title or "Modbus Register Test Raporu").font = Font(bold=True, size=16)
    ws.cell(2, 1, f"Başlangıç: {run['started']}   Bitiş: {run['finished']}")
    info = "   ".join(f"{k}: {v}" for k, v in _settings_text(run["settings"]))
    ws.cell(3, 1, info)
    s = run["summary"].get("overall", {})
    ws.cell(4, 1, f"Toplam {run['summary'].get('total', 0)} register  —  OK: {s.get(OK, 0)}   UYARI: {s.get(WARN, 0)}   HATA: {s.get(FAIL, 0)}   ATLANDI: {s.get(SKIP, 0)}"
            + (f"   ⚠ {run['aborted']}" if run.get("aborted") else "")).font = Font(bold=True)
    hr = 6
    for i, (_, label) in enumerate(cols, 1):
        c = ws.cell(hr, i, label)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = head_fill if i <= len(SRC_COLS) else res_fill
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = border
    ws.row_dimensions[hr].height = 32
    r = hr + 1
    for kind, item in _rows_with_sections(run, regs):
        if kind == "section":
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncol)
            c = ws.cell(r, 1, item)
            c.font = Font(bold=True, size=12)
            c.fill = sec_fill
            c.alignment = Alignment(horizontal="center")
            r += 1
            continue
        for i, (k, _) in enumerate(cols, 1):
            v = item[k]
            if k in ("address", "words", "min", "max") and v.lstrip("-").isdigit():
                v = int(v)
            c = ws.cell(r, i, v)
            c.border = border
            c.alignment = Alignment(vertical="center", wrap_text=k in ("remarks", "notes", "name"),
                                    horizontal="center" if k in STATUS_KEYS or k in ("address", "hex", "fmt", "words", "access_label") else "left")
            if k in STATUS_KEYS and item[k] in COLORS:
                c.fill = PatternFill("solid", fgColor=COLORS[item[k]])
                c.font = Font(bold=k == "overall", color=FONT_COLORS[item[k]])
        r += 1
    widths = {"address": 9, "hex": 9, "fmt": 9, "words": 7, "mcu_byte": 6, "name": 28, "unit": 9, "access_label": 6,
              "remarks": 28, "multiplier": 8, "na_value": 10, "min": 7, "max": 7, "value": 10, "raw": 11,
              "written": 14, "readback": 14, "block_read_group": 13, "block_write_group": 13, "notes": 60}
    for i, (k, _) in enumerate(cols, 1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(k, 12)
    ws.freeze_panes = f"G{hr + 1}"
    ws.auto_filter.ref = f"A{hr}:{get_column_letter(ncol)}{r - 1}"

    # Ozet sayfasi
    ws2 = wb.create_sheet("Özet")
    ws2.append(["Test", OK, WARN, FAIL, SKIP])
    for k in ["read", "write", "block_read", "block_write", "ro_protect", "range_protect", "overall"]:
        d = run["summary"].get(k, {})
        ws2.append([TEST_NAMES[k], d.get(OK, 0), d.get(WARN, 0), d.get(FAIL, 0), d.get(SKIP, 0)])
    for c in ws2[1]:
        c.font = Font(bold=True)
    ws2.append([])
    ws2.append(["İstek sayısı", run["summary"].get("stats", {}).get("requests", "")])
    ws2.append(["Zaman aşımı", run["summary"].get("stats", {}).get("timeouts", "")])
    ws2.append(["Çerçeve/CRC hatası", run["summary"].get("stats", {}).get("errors", "")])
    ws2.column_dimensions["A"].width = 24
    ws3 = wb.create_sheet("Ayarlar")
    for k, v in _settings_text(run["settings"]):
        ws3.append([k, str(v)])
    ws3.append([])
    for k, v in run["options"].items():
        ws3.append([k, str(v)])
    ws3.column_dimensions["A"].width = 24
    wb.save(path)


CSS = """
:root{--ok:#c6efce;--okt:#006100;--fail:#ffc7ce;--failt:#9c0006;--warn:#ffeb9c;--warnt:#7f6000;--skip:#e7e6e6;--skipt:#595959}
body{font-family:Segoe UI,Arial,sans-serif;font-size:12px;margin:16px;color:#222;background:#fff}
h1{font-size:20px;margin:0 0 6px}
.meta{margin:2px 0;color:#444}
.sum{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}
.sum span{padding:6px 10px;border-radius:4px;font-weight:600}
table{border-collapse:collapse;width:100%}
th,td{border:1px solid #aaa;padding:3px 5px;vertical-align:middle}
th{background:#1f3864;color:#fff;position:sticky;top:0;font-weight:600}
th.res{background:#2e75b6}
tr.sec td{background:#d9e1f2;font-weight:700;text-align:center;font-size:13px}
td.c{text-align:center}
.OK{background:var(--ok);color:var(--okt)} .HATA{background:var(--fail);color:var(--failt);font-weight:600}
.UYARI{background:var(--warn);color:var(--warnt)} .ATLANDI{background:var(--skip);color:var(--skipt)}
.filters button{margin-right:4px}
@media print{th{position:static}.filters{display:none}body{margin:0;font-size:8px}}
"""


def register_report_html(run: dict, regs: List[dict], path: str, title: str = ""):
    e = html.escape
    s = run["summary"].get("overall", {})
    out = io.StringIO()
    out.write(f"<!doctype html><html lang='tr'><head><meta charset='utf-8'><title>{e(title or 'Modbus Register Test Raporu')}</title><style>{CSS}</style></head><body>")
    out.write(f"<h1>{e(title or 'Modbus Register Test Raporu')}</h1>")
    out.write(f"<div class='meta'>Başlangıç: {e(run['started'])} &nbsp; Bitiş: {e(run['finished'])}</div>")
    out.write("<div class='meta'>" + " &nbsp;·&nbsp; ".join(f"<b>{e(k)}:</b> {e(str(v))}" for k, v in _settings_text(run["settings"])) + "</div>")
    if run.get("aborted"):
        out.write(f"<div class='meta HATA'>⚠ {e(run['aborted'])}</div>")
    out.write("<div class='sum'>" + f"<span>Toplam: {run['summary'].get('total', 0)}</span>" +
              "".join(f"<span class='{k}'>{k}: {s.get(k, 0)}</span>" for k in (OK, WARN, FAIL, SKIP)) + "</div>")
    out.write("<table style='width:auto;margin-bottom:10px'><tr><th>Test</th><th>OK</th><th>UYARI</th><th>HATA</th><th>ATLANDI</th></tr>")
    for k in ["read", "write", "block_read", "block_write", "ro_protect", "range_protect"]:
        d = run["summary"].get(k, {})
        out.write(f"<tr><td>{TEST_NAMES[k]}</td>" + "".join(f"<td class='c'>{d.get(x, 0)}</td>" for x in (OK, WARN, FAIL, SKIP)) + "</tr>")
    out.write("</table>")
    out.write("<div class='filters'>Filtre: <button onclick=\"f('')\">Tümü</button><button onclick=\"f('HATA')\">Sadece HATA</button>"
              "<button onclick=\"f('UYARI')\">HATA+UYARI</button></div><br>")
    cols = SRC_COLS + RES_COLS
    out.write("<table id='t'><thead><tr>" + "".join(
        f"<th class='{'res' if i >= len(SRC_COLS) else ''}'>{e(l)}</th>" for i, (_, l) in enumerate(cols)) + "</tr></thead><tbody>")
    for kind, item in _rows_with_sections(run, regs):
        if kind == "section":
            out.write(f"<tr class='sec'><td colspan='{len(cols)}'>{e(item)}</td></tr>")
            continue
        out.write(f"<tr data-s='{e(item['overall'])}'>")
        for k, _ in cols:
            v = item[k]
            cls = v if k in STATUS_KEYS and v in COLORS else ""
            if k in ("address", "hex", "fmt", "words", "access_label", "mcu_byte", "min", "max", "multiplier") or k in STATUS_KEYS:
                cls += " c"
            out.write(f"<td class='{cls}'>{e(v)}</td>")
        out.write("</tr>")
    out.write("</tbody></table><script>function f(s){document.querySelectorAll('#t tbody tr').forEach(function(r){var v=r.dataset.s;"
              "if(!v){r.style.display=s?'none':'';return}r.style.display=(!s||v==s||(s=='UYARI'&&v=='HATA'))?'':'none'})}</script></body></html>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(out.getvalue())


def register_report_csv(run: dict, regs: List[dict], path: str):
    cols = SRC_COLS + RES_COLS
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["Section"] + [l for _, l in cols])
        sec = ""
        for kind, item in _rows_with_sections(run, regs):
            if kind == "section":
                sec = item
                continue
            w.writerow([sec] + [item[k] for k, _ in cols])


# --------------------------------------------------------------------------
# Ayar tarama raporu
# --------------------------------------------------------------------------
SCAN_COLS = [("no", "#"), ("purpose", "Amaç"), ("param", "Parametre"), ("register", "Register"), ("raw", "Yazılan Ham Değer"),
             ("setting", "Ayar"), ("from_settings", "Önceki Hat Ayarı"), ("to_settings", "Yeni Hat Ayarı"),
             ("write_resp", "Yazma Yanıtı"), ("verify", "Yeni Ayarla Doğrulama"), ("old_silent", "Eski Ayar Sessiz"),
             ("functional", "Fonksiyon Testi"), ("status", "SONUÇ"), ("duration", "Süre (sn)"), ("note", "Açıklama")]
SCAN_STATUS = {"verify", "old_silent", "functional", "status"}


def scan_report_xlsx(run: dict, path: str, title: str = ""):
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Ayar Tarama Raporu"
    thin = Side(style="thin", color="999999")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    ws.cell(1, 1, title or "Modbus Haberleşme Ayarları Tarama Raporu").font = Font(bold=True, size=16)
    ws.cell(2, 1, f"Başlangıç: {run['started']}   Bitiş: {run['finished']}")
    ws.cell(3, 1, "Başlangıç ayarı: " + "   ".join(f"{k}: {v}" for k, v in _settings_text(run["start_settings"])))
    ws.cell(4, 1, f"Son hat ayarı: {run['final_settings']}")
    sm = run["summary"]
    ws.cell(5, 1, f"{sm.get('steps', 0)} adım — OK: {sm.get(OK, 0)}  UYARI: {sm.get(WARN, 0)}  HATA: {sm.get(FAIL, 0)}"
            + (f"   ⚠ {run['aborted']}" if run.get("aborted") else "")).font = Font(bold=True)
    hr = 7
    for i, (_, l) in enumerate(SCAN_COLS, 1):
        c = ws.cell(hr, i, l)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3864")
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = border
    for ri, st in enumerate(run["steps"], hr + 1):
        for i, (k, _) in enumerate(SCAN_COLS, 1):
            v = st.get(k, "")
            c = ws.cell(ri, i, v)
            c.border = border
            c.alignment = Alignment(wrap_text=k == "note", vertical="center")
            if k in SCAN_STATUS and v in COLORS:
                c.fill = PatternFill("solid", fgColor=COLORS[v])
                c.font = Font(color=FONT_COLORS[v], bold=k == "status")
    widths = [4, 10, 13, 9, 9, 9, 16, 16, 22, 11, 10, 10, 9, 8, 70]
    for i, wdt in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = wdt
    ws.freeze_panes = f"A{hr + 1}"
    ws2 = wb.create_sheet("Parametreler")
    ws2.append(["Parametre", "Register", "Orijinal Ham Değer", "Test Değerleri (ham=ayar)"])
    for p in run["params"]:
        ws2.append([p["kind"], p["register"], run["original"].get(p["kind"], ""),
                    ", ".join(f"{v['raw']}={v['setting']}" for v in p["values"]) + ("" if p.get("enabled", True) else " (devre dışı)")])
    ws2.append([])
    for k, v in run["options"].items():
        ws2.append([k, str(v)])
    ws2.column_dimensions["A"].width = 20
    ws2.column_dimensions["D"].width = 60
    wb.save(path)


def scan_report_html(run: dict, path: str, title: str = ""):
    e = html.escape
    sm = run["summary"]
    o = io.StringIO()
    t = title or "Modbus Haberleşme Ayarları Tarama Raporu"
    o.write(f"<!doctype html><html lang='tr'><head><meta charset='utf-8'><title>{e(t)}</title><style>{CSS}</style></head><body><h1>{e(t)}</h1>")
    o.write(f"<div class='meta'>Başlangıç: {e(run['started'])} &nbsp; Bitiş: {e(run['finished'])}</div>")
    o.write("<div class='meta'>Başlangıç ayarı: " + " &nbsp;·&nbsp; ".join(f"<b>{e(k)}:</b> {e(str(v))}" for k, v in _settings_text(run["start_settings"])) + "</div>")
    o.write(f"<div class='meta'><b>Son hat ayarı:</b> {e(run['final_settings'])}</div>")
    if run.get("aborted"):
        o.write(f"<div class='meta HATA'>⚠ {e(run['aborted'])}</div>")
    o.write(f"<div class='sum'><span>{sm.get('steps', 0)} adım</span>" + "".join(f"<span class='{k}'>{k}: {sm.get(k, 0)}</span>" for k in (OK, WARN, FAIL)) + "</div>")
    o.write("<table style='width:auto;margin-bottom:10px'><tr><th>Parametre</th><th>Register</th><th>Orijinal</th><th>Test değerleri (ham=ayar)</th></tr>")
    for p in run["params"]:
        o.write(f"<tr><td>{e(p['kind'])}</td><td>{p['register']}</td><td>{e(str(run['original'].get(p['kind'], '')))}</td><td>"
                + e(", ".join(f"{v['raw']}={v['setting']}" for v in p["values"])) + ("" if p.get("enabled", True) else " (devre dışı)") + "</td></tr>")
    o.write("</table><table><thead><tr>" + "".join(f"<th>{e(l)}</th>" for _, l in SCAN_COLS) + "</tr></thead><tbody>")
    for st in run["steps"]:
        o.write("<tr>" + "".join(
            f"<td class='{e(str(st.get(k, ''))) if k in SCAN_STATUS else ''} {'c' if k != 'note' else ''}'>{e(str(st.get(k, '')))}</td>" for k, _ in SCAN_COLS) + "</tr>")
    o.write("</tbody></table></body></html>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(o.getvalue())


def scan_report_csv(run: dict, path: str):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow([l for _, l in SCAN_COLS])
        for st in run["steps"]:
            w.writerow([st.get(k, "") for k, _ in SCAN_COLS])


def write_reports(kind: str, run: dict, outdir: str, base: str, regs: List[dict] = None, title: str = "") -> Dict[str, str]:
    os.makedirs(outdir, exist_ok=True)
    paths = {}
    p = os.path.join(outdir, base)
    if kind == "test":
        register_report_xlsx(run, regs, p + ".xlsx", title)
        register_report_html(run, regs, p + ".html", title)
        register_report_csv(run, regs, p + ".csv")
    else:
        scan_report_xlsx(run, p + ".xlsx", title)
        scan_report_html(run, p + ".html", title)
        scan_report_csv(run, p + ".csv")
    with open(p + ".json", "w", encoding="utf-8") as fh:
        json.dump(run, fh, ensure_ascii=False, indent=1, default=str)
    for ext in ("xlsx", "html", "csv", "json"):
        paths[ext] = p + "." + ext
    return paths

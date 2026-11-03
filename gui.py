"""Modbus Cihaz Test Araci - masaustu uygulamasi (Tkinter).

Calistirma: python gui.py   (veya derlenmis ModbusTester.exe)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

if getattr(sys, "frozen", False):
    BASE = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from modbus_tester.table import load_table, save_csv, default_comm_config, encode_value, decode_value, fmt_value  # noqa: E402
from modbus_tester.rtu import LineSettings, ModbusClient, ModbusError  # noqa: E402
from modbus_tester.simulator import SimDevice  # noqa: E402
from modbus_tester.tester import TestOptions, run_register_test, summarize, make_groups  # noqa: E402
from modbus_tester.commscan import ScanOptions, run_comm_scan  # noqa: E402
from modbus_tester.jobs import Job  # noqa: E402
from modbus_tester.report import write_reports  # noqa: E402

APP_NAME = "Modbus Cihaz Test Aracı"


def user_dir() -> str:
    if os.name == "nt":
        root = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        root = os.path.join(os.path.expanduser("~"), ".config")
    d = os.path.join(root, "ModbusTester")
    os.makedirs(d, exist_ok=True)
    return d


def report_dir() -> str:
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    if not os.path.isdir(docs):
        docs = os.path.expanduser("~")
    d = os.path.join(docs, "ModbusTester Raporlar")
    os.makedirs(d, exist_ok=True)
    return d


def open_path(p: str):
    try:
        if os.name == "nt":
            os.startfile(p)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p])
    except Exception as e:
        messagebox.showerror(APP_NAME, f"Açılamadı: {p}\n{e}")


def list_serial_ports():
    try:
        from serial.tools import list_ports
        return [(p.device, p.description) for p in list_ports.comports()]
    except Exception:
        return []


TRANSPORTS = [("Modbus RTU (seri port / RS485)", "rtu"), ("Modbus TCP", "tcp"), ("Modbus RTU over TCP", "rtu_over_tcp"),
              ("Simülatör (cihazsız deneme)", "sim")]
PARITIES = [("None", "N"), ("Even", "E"), ("Odd", "O")]
BAUDS = ["1200", "2400", "4800", "9600", "14400", "19200", "38400", "57600", "115200"]
KIND_LABEL = {"address": "Modbus adres", "baud": "Baud rate", "parity": "Parity", "stopbits": "Stop bit", "databits": "Data bit"}
STATUS_TAG = {"OK": "ok", "HATA": "fail", "UYARI": "warn", "ATLANDI": "skip"}


class ScrollFrame(ttk.Frame):
    """Dikey kaydirilabilir cerceve."""

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        vs = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.configure(yscrollcommand=vs.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1320x860")
        self.minsize(1050, 700)
        try:
            self.tk.call("tk", "scaling", self.winfo_fpixels("1i") / 72.0)
        except tk.TclError:
            pass
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("Treeview", rowheight=22)

        self.regs = []
        self.table_name = ""
        self.warnings = []
        self.sim = None
        self.sim_key = None
        self.job: Job | None = None
        self.log_pos = 0
        self.params = []
        self.param_rows = []
        self.cfg_path = os.path.join(user_dir(), "ayarlar.json")

        self._build()
        self._load_cfg()
        self.refresh_ports()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(200, self.poll)

    # ------------------------------------------------------------------ UI
    def _build(self):
        pw = ttk.PanedWindow(self, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=8, pady=8)
        left = ttk.Frame(pw, width=330)
        right = ttk.Frame(pw)
        pw.add(left, weight=0)
        pw.add(right, weight=1)
        self._build_conn(left)
        self._build_table(left)

        rp = ttk.PanedWindow(right, orient="vertical")
        rp.pack(fill="both", expand=True)
        top = ttk.Frame(rp)
        bottom = ttk.Frame(rp)
        rp.add(top, weight=3)
        rp.add(bottom, weight=2)
        self.nb = ttk.Notebook(top)
        self.nb.pack(fill="both", expand=True)
        self._build_test_tab()
        self._build_scan_tab()
        self._build_regs_tab()
        self._build_manual_tab()
        self._build_results_tab()
        self._build_status(bottom)

    def _build_conn(self, parent):
        f = ttk.LabelFrame(parent, text=" 1. Bağlantı ayarları ", padding=8)
        f.pack(fill="x")
        f.columnconfigure(1, weight=1)
        self.v_transport = tk.StringVar(value=TRANSPORTS[0][0])
        self.v_port = tk.StringVar()
        self.v_slave = tk.StringVar(value="1")
        self.v_baud = tk.StringVar(value="9600")
        self.v_parity = tk.StringVar(value="None")
        self.v_bytesize = tk.StringVar(value="8")
        self.v_stopbits = tk.StringVar(value="1")
        self.v_timeout = tk.StringVar(value="500")
        self.v_retries = tk.StringVar(value="1")
        self.v_delay = tk.StringVar(value="20")
        self.v_simfaults = tk.BooleanVar(value=False)
        r = 0

        def row(label, widget):
            nonlocal r
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=2, padx=(0, 6))
            widget.grid(row=r, column=1, sticky="ew", pady=2)
            r += 1
            return widget

        cb = row("Bağlantı tipi", ttk.Combobox(f, textvariable=self.v_transport, values=[t[0] for t in TRANSPORTS], state="readonly"))
        cb.bind("<<ComboboxSelected>>", lambda e: self.update_conn_ui())
        pf = ttk.Frame(f)
        pf.columnconfigure(0, weight=1)
        self.cb_port = ttk.Combobox(pf, textvariable=self.v_port)
        self.cb_port.grid(row=0, column=0, sticky="ew")
        self.btn_ports = ttk.Button(pf, text="Yenile", width=7, command=self.refresh_ports)
        self.btn_ports.grid(row=0, column=1, padx=(4, 0))
        self.lbl_port = ttk.Label(f, text="Seri port")
        self.lbl_port.grid(row=r, column=0, sticky="w", pady=2)
        pf.grid(row=r, column=1, sticky="ew", pady=2)
        r += 1
        row("Slave adresi", ttk.Spinbox(f, from_=0, to=247, textvariable=self.v_slave, width=8))
        self.w_baud = row("Baud rate", ttk.Combobox(f, textvariable=self.v_baud, values=BAUDS))
        self.w_parity = row("Parity", ttk.Combobox(f, textvariable=self.v_parity, values=[p[0] for p in PARITIES], state="readonly"))
        self.w_bytesize = row("Data bit", ttk.Combobox(f, textvariable=self.v_bytesize, values=["8", "7"], state="readonly"))
        self.w_stop = row("Stop bit", ttk.Combobox(f, textvariable=self.v_stopbits, values=["1", "2"], state="readonly"))
        row("Zaman aşımı (ms)", ttk.Spinbox(f, from_=20, to=10000, increment=50, textvariable=self.v_timeout, width=8))
        row("Tekrar sayısı", ttk.Spinbox(f, from_=0, to=5, textvariable=self.v_retries, width=8))
        row("İstekler arası (ms)", ttk.Spinbox(f, from_=0, to=2000, increment=5, textvariable=self.v_delay, width=8))
        self.chk_sim = ttk.Checkbutton(f, text="Simülatöre örnek hatalar ekle", variable=self.v_simfaults)
        self.chk_sim.grid(row=r, column=0, columnspan=2, sticky="w")
        r += 1
        pr = ttk.Frame(f)
        pr.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        r += 1
        self.v_probe = tk.StringVar()
        ttk.Label(pr, text="Register:").pack(side="left")
        ttk.Entry(pr, textvariable=self.v_probe, width=8).pack(side="left", padx=4)
        ttk.Button(pr, text="Bağlantıyı dene", command=self.probe).pack(side="left")
        self.lbl_probe = ttk.Label(f, text="", wraplength=300, foreground="#555")
        self.lbl_probe.grid(row=r, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _build_table(self, parent):
        f = ttk.LabelFrame(parent, text=" 2. Register tablosu ", padding=8)
        f.pack(fill="both", expand=True, pady=(8, 0))
        ttk.Label(f, text="PDF, Excel (.xlsx) veya CSV.\nKolonlar: Address, Address Hex, Format, Word Counts,\n"
                          "Designation, Unit, R/W, Remarks, Multiplier,\nValue If Not Available, Min, Max",
                  foreground="#555", justify="left").pack(anchor="w")
        b = ttk.Frame(f)
        b.pack(fill="x", pady=6)
        ttk.Button(b, text="Tablo yükle...", style="Accent.TButton", command=self.load_table_dialog).pack(side="left")
        ttk.Button(b, text="CSV olarak kaydet", command=self.export_csv).pack(side="left", padx=6)
        self.lbl_table = ttk.Label(f, text="Tablo yüklenmedi", wraplength=300)
        self.lbl_table.pack(anchor="w")
        ttk.Label(f, text="Tablo uyarıları:").pack(anchor="w", pady=(6, 0))
        self.txt_warn = tk.Text(f, height=8, wrap="word", font=("Segoe UI", 9), background="#fff8e1", relief="flat")
        self.txt_warn.pack(fill="both", expand=True)
        self.txt_warn.configure(state="disabled")

    def _opt_check(self, parent, text, var, r, c):
        ttk.Checkbutton(parent, text=text, variable=var).grid(row=r, column=c, sticky="w", pady=1, padx=(0, 16))

    def _opt_entry(self, parent, text, var, r, c, values=None):
        fr = ttk.Frame(parent)
        fr.grid(row=r, column=c, sticky="w", pady=2)
        ttk.Label(fr, text=text, width=34).pack(side="left")
        if values:
            ttk.Combobox(fr, textvariable=var, values=values, state="readonly", width=32).pack(side="left")
        else:
            ttk.Entry(fr, textvariable=var, width=10).pack(side="left")

    def _build_test_tab(self):
        t = ttk.Frame(self.nb, padding=10)
        self.nb.add(t, text="  3a. Register testi  ")
        self.o = {
            "single_write": tk.BooleanVar(value=True), "test_boundaries": tk.BooleanVar(value=True),
            "block_read": tk.BooleanVar(value=True), "block_write": tk.BooleanVar(value=True),
            "restore": tk.BooleanVar(value=True), "ro_protection": tk.BooleanVar(value=True),
            "range_protection": tk.BooleanVar(value=False), "write_only_regs": tk.BooleanVar(value=False),
            "skip_comm": tk.BooleanVar(value=True), "word_order_big": tk.BooleanVar(value=True),
            "read_fc": tk.StringVar(value="FC03 Holding Register"), "block_size": tk.StringVar(value="16"),
            "settle_ms": tk.StringVar(value="0"), "address_offset": tk.StringVar(value="0"),
        }
        o = self.o
        g = ttk.LabelFrame(t, text=" Testler ", padding=8)
        g.pack(fill="x")
        self._opt_check(g, "Tekli yazma + geri okuma doğrulaması (R/W)", o["single_write"], 0, 0)
        self._opt_check(g, "Min ve Max değerlerinin ikisini de yaz", o["test_boundaries"], 1, 0)
        self._opt_check(g, "Blok okuma (bitişik registerlar)", o["block_read"], 2, 0)
        self._opt_check(g, "Blok yazma (FC16) + blok geri okuma", o["block_write"], 3, 0)
        self._opt_check(g, "Testten sonra orijinal değerleri geri yaz", o["restore"], 4, 0)
        self._opt_check(g, "Salt okunur (R) registerlar yazmayı reddetmeli", o["ro_protection"], 0, 1)
        self._opt_check(g, "Aralık dışı değer (Min-1 / Max+1) reddedilmeli", o["range_protection"], 1, 1)
        self._opt_check(g, "Sadece yazılabilir (W) registerlara yaz", o["write_only_regs"], 2, 1)
        self._opt_check(g, "Haberleşme ayar registerlarını yazma testinden çıkar", o["skip_comm"], 3, 1)
        self._opt_check(g, "32-bit değerlerde yüksek word önce", o["word_order_big"], 4, 1)
        g2 = ttk.LabelFrame(t, text=" Parametreler ", padding=8)
        g2.pack(fill="x", pady=8)
        self._opt_entry(g2, "R registerlar için okuma fonksiyonu", o["read_fc"], 0, 0,
                        ["FC03 Holding Register", "FC04 Input Register"])
        self._opt_entry(g2, "Blok boyutu (register, 2-123)", o["block_size"], 1, 0)
        self._opt_entry(g2, "Yazma sonrası bekleme (ms)", o["settle_ms"], 2, 0)
        self._opt_entry(g2, "Adres ofseti (tablo − ofset = protokol adr.)", o["address_offset"], 3, 0)
        b = ttk.Frame(t)
        b.pack(fill="x")
        self.v_title1 = tk.StringVar()
        ttk.Label(b, text="Rapor başlığı:").pack(side="left")
        ttk.Entry(b, textvariable=self.v_title1, width=50).pack(side="left", padx=6, fill="x", expand=True)
        self.btn_test = ttk.Button(b, text="Testi başlat", style="Accent.TButton", command=self.start_test)
        self.btn_test.pack(side="right")

    def _build_scan_tab(self):
        t = ttk.Frame(self.nb, padding=10)
        self.nb.add(t, text="  3b. Haberleşme ayarı tarama  ")
        ttk.Label(t, wraplength=900, foreground="#444", justify="left", text=(
            "Cihazın haberleşme parametrelerini sırayla değiştirir; her değişiklikten sonra yeni ayarla bağlanıp cihazın yanıt "
            "verdiğini, register değerini ve kısa bir fonksiyon testini doğrular, sonunda orijinal ayarlara döner. "
            "Başlangıç ayarı soldaki bağlantı ayarlarıdır. Test değerleri 'ham=ayar' biçimindedir, örn. 0=4800, 1=9600, 2=19200 "
            "veya parity için 0=N, 1=O, 2=E.")).pack(anchor="w")
        pf = ttk.LabelFrame(t, text=" Parametreler ", padding=6)
        pf.pack(fill="x", pady=6)
        hdr = ttk.Frame(pf)
        hdr.pack(fill="x")
        for txt, w in (("Kullan", 7), ("Parametre", 14), ("Register", 10), ("Test değerleri (ham=ayar, virgülle)", 40)):
            ttk.Label(hdr, text=txt, width=w, font=("Segoe UI", 9, "bold")).pack(side="left", padx=2)
        self.param_box = ttk.Frame(pf)
        self.param_box.pack(fill="x")
        ab = ttk.Frame(pf)
        ab.pack(fill="x", pady=(4, 0))
        self.v_newkind = tk.StringVar(value=KIND_LABEL["address"])
        ttk.Combobox(ab, textvariable=self.v_newkind, values=list(KIND_LABEL.values()), state="readonly", width=16).pack(side="left")
        ttk.Button(ab, text="+ Parametre ekle", command=self.add_param).pack(side="left", padx=4)
        self.s = {
            "mode": tk.StringVar(value="Sıralı (her parametre tek tek)"), "settle_ms": tk.StringVar(value="1500"),
            "verify_retries": tk.StringVar(value="3"), "apply_register": tk.StringVar(value=""),
            "functional_check": tk.BooleanVar(value=True), "check_old_silent": tk.BooleanVar(value=False),
            "recovery_search": tk.BooleanVar(value=True), "restore": tk.BooleanVar(value=True),
            "parity_none_2stop": tk.BooleanVar(value=False),
        }
        s = self.s
        g = ttk.LabelFrame(t, text=" Seçenekler ", padding=8)
        g.pack(fill="x")
        self._opt_entry(g, "Mod", s["mode"], 0, 0, ["Sıralı (her parametre tek tek)", "Tüm kombinasyonlar"])
        self._opt_entry(g, "Ayar sonrası bekleme (ms)", s["settle_ms"], 1, 0)
        self._opt_entry(g, "Doğrulama deneme sayısı", s["verify_retries"], 2, 0)
        self._opt_entry(g, "Ayarı uygulama registerı (örn. 17130=1)", s["apply_register"], 3, 0)
        self._opt_check(g, "Her ayarda fonksiyon testi", s["functional_check"], 0, 1)
        self._opt_check(g, "Eski ayarla yanıt vermediğini kontrol et", s["check_old_silent"], 1, 1)
        self._opt_check(g, "Cihaz kaybolursa bilinen ayarlarda ara", s["recovery_search"], 2, 1)
        self._opt_check(g, "Sonunda orijinal ayarlara dön", s["restore"], 3, 1)
        self._opt_check(g, "Parity None iken 2 stop bit", s["parity_none_2stop"], 4, 1)
        b = ttk.Frame(t)
        b.pack(fill="x", pady=(8, 0))
        self.v_title2 = tk.StringVar()
        ttk.Label(b, text="Rapor başlığı:").pack(side="left")
        ttk.Entry(b, textvariable=self.v_title2, width=50).pack(side="left", padx=6, fill="x", expand=True)
        self.btn_scan = ttk.Button(b, text="Taramayı başlat", style="Accent.TButton", command=self.start_scan)
        self.btn_scan.pack(side="right")
        self.render_params()

    def _build_regs_tab(self):
        t = ttk.Frame(self.nb, padding=6)
        self.nb.add(t, text="  Register listesi  ")
        b = ttk.Frame(t)
        b.pack(fill="x", pady=(0, 4))
        self.v_filter = tk.StringVar()
        ttk.Label(b, text="Ara:").pack(side="left")
        e = ttk.Entry(b, textvariable=self.v_filter, width=30)
        e.pack(side="left", padx=4)
        self.v_filter.trace_add("write", lambda *a: self.render_regs())
        ttk.Button(b, text="Seçilileri atla", command=lambda: self.set_skip(True)).pack(side="left", padx=2)
        ttk.Button(b, text="Seçilileri dahil et", command=lambda: self.set_skip(False)).pack(side="left", padx=2)
        ttk.Label(b, text="(Çift tıklama: atla/dahil et. Ctrl/Shift ile çoklu seçim)", foreground="#666").pack(side="left", padx=8)
        cols = ("skip", "address", "hex", "fmt", "name", "access", "min", "max", "section", "remarks")
        heads = ("Atla", "Address", "Hex", "Format", "Designation", "R/W", "Min", "Max", "Bölüm", "Remarks")
        widths = (45, 70, 60, 70, 230, 50, 55, 55, 150, 300)
        self.tv_regs = self._tree(t, cols, heads, widths)
        self.tv_regs.tag_configure("skip", foreground="#999")
        self.tv_regs.tag_configure("comm", background="#fff4d6")
        self.tv_regs.bind("<Double-1>", self.toggle_skip)

    def _build_manual_tab(self):
        t = ttk.Frame(self.nb, padding=6)
        self.tab_manual = t
        self.nb.add(t, text="  Manuel okuma / yazma  ")
        top = ttk.Frame(t)
        top.pack(fill="x", pady=(0, 4))
        ttk.Button(top, text="Seçilileri oku", command=lambda: self.manual_read(selected=True)).pack(side="left")
        ttk.Button(top, text="Tümünü oku", command=lambda: self.manual_read(selected=False)).pack(side="left", padx=4)
        self.v_mpoll = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Sürekli oku, her", variable=self.v_mpoll, command=self.manual_poll_toggle).pack(side="left", padx=(12, 2))
        self.v_mpoll_ms = tk.StringVar(value="1000")
        ttk.Spinbox(top, from_=200, to=60000, increment=100, textvariable=self.v_mpoll_ms, width=7).pack(side="left")
        ttk.Label(top, text="ms (seçili varsa seçilileri, yoksa listedekileri)").pack(side="left", padx=4)
        self.v_mfilter = tk.StringVar()
        ttk.Label(top, text="Ara:").pack(side="left", padx=(16, 2))
        ttk.Entry(top, textvariable=self.v_mfilter, width=20).pack(side="left")
        self.v_mfilter.trace_add("write", lambda *a: self.render_manual())

        wr = ttk.LabelFrame(t, text=" Seçili registera yaz ", padding=6)
        wr.pack(fill="x", pady=(0, 4))
        self.lbl_msel = ttk.Label(wr, text="Register seçin (çift tıklama değeri buraya getirir)", width=44)
        self.lbl_msel.pack(side="left")
        ttk.Label(wr, text="Değer:").pack(side="left", padx=(8, 2))
        self.v_mval = tk.StringVar()
        ent = ttk.Entry(wr, textvariable=self.v_mval, width=14)
        ent.pack(side="left")
        ent.bind("<Return>", lambda e: self.manual_write())
        self.v_mfc16 = tk.BooleanVar(value=False)
        ttk.Checkbutton(wr, text="FC16 ile yaz", variable=self.v_mfc16).pack(side="left", padx=6)
        ttk.Button(wr, text="Yaz + geri oku", style="Accent.TButton", command=self.manual_write).pack(side="left", padx=4)

        raw = ttk.LabelFrame(t, text=" Serbest adres (tabloda olmayan registerlar dahil) ", padding=6)
        raw.pack(fill="x", pady=(0, 4))
        self.v_raddr = tk.StringVar(value="")
        self.v_rcount = tk.StringVar(value="1")
        self.v_rfc = tk.StringVar(value="FC03")
        self.v_rvals = tk.StringVar(value="")
        ttk.Label(raw, text="Adres:").pack(side="left")
        ttk.Entry(raw, textvariable=self.v_raddr, width=8).pack(side="left", padx=2)
        ttk.Label(raw, text="Adet:").pack(side="left", padx=(6, 0))
        ttk.Spinbox(raw, from_=1, to=125, textvariable=self.v_rcount, width=5).pack(side="left", padx=2)
        ttk.Combobox(raw, textvariable=self.v_rfc, values=["FC03", "FC04"], state="readonly", width=6).pack(side="left", padx=2)
        ttk.Button(raw, text="Oku", command=self.raw_read).pack(side="left", padx=4)
        ttk.Label(raw, text="Değerler:").pack(side="left", padx=(12, 2))
        ttk.Entry(raw, textvariable=self.v_rvals, width=18).pack(side="left", padx=2)
        ttk.Button(raw, text="Yaz", command=self.raw_write).pack(side="left", padx=4)
        ttk.Label(raw, text="(virgülle; tek değer FC06, çok değer FC16)", foreground="#666").pack(side="left")

        cols = ("address", "hex", "name", "access", "min", "max", "value", "raw", "time", "status")
        heads = ("Address", "Hex", "Designation", "R/W", "Min", "Max", "Değer", "Ham", "Zaman", "Son işlem")
        widths = (70, 60, 230, 50, 55, 55, 80, 120, 70, 420)
        self.tv_man = self._tree(t, cols, heads, widths)
        self.tv_man.bind("<<TreeviewSelect>>", self.manual_select)
        self.tv_man.bind("<Double-1>", self.manual_pick)
        self.man_state = {}   # addr -> dict(value, raw, time, status, tag)
        self.man_busy = False
        self._mpoll_after = None

    # ---- manuel okuma/yazma
    def render_manual(self):
        tv = self.tv_man
        tv.delete(*tv.get_children())
        q = self.v_mfilter.get().lower().strip()
        sec = None
        for r in self.regs:
            if q and q not in f"{r.address} {r.hex} {r.name} {r.section}".lower():
                continue
            if r.section != sec:
                sec = r.section
                tv.insert("", "end", iid=f"sec_{r.address}", values=("", "", sec or ""), tags=("sec",))
            st = self.man_state.get(r.address, {})
            tv.insert("", "end", iid=str(r.address), tags=(st.get("tag", ""),), values=self._man_row(r, st))

    def _man_row(self, r, st):
        return (r.address, r.hex, r.name, {"RW": "R/W"}.get(r.access, r.access or "-"), self._fv(r.min), self._fv(r.max),
                st.get("value", ""), st.get("raw", ""), st.get("time", ""), st.get("status", ""))

    def _man_set(self, r, **kw):
        st = self.man_state.setdefault(r.address, {})
        st.update(kw)
        st["time"] = time.strftime("%H:%M:%S")
        iid = str(r.address)
        if self.tv_man.exists(iid):
            self.tv_man.item(iid, values=self._man_row(r, st), tags=(st.get("tag", ""),))

    def manual_select(self, ev=None):
        sel = [self._reg(i) for i in self.tv_man.selection()]
        sel = [r for r in sel if r]
        if len(sel) == 1:
            r = sel[0]
            rng = f"  [{self._fv(r.min)}..{self._fv(r.max)}]" if r.min is not None else ""
            self.lbl_msel.configure(text=f"{r.address} {r.name} ({ {'RW': 'R/W'}.get(r.access, r.access)}){rng}")
        elif sel:
            self.lbl_msel.configure(text=f"{len(sel)} register seçili (yazma tek tek uygulanır)")

    def manual_pick(self, ev):
        iid = self.tv_man.identify_row(ev.y)
        r = self._reg(iid) if iid else None
        if r:
            self.v_mval.set(self.man_state.get(r.address, {}).get("value", ""))

    def _manual_ctx(self):
        if self.busy():
            messagebox.showinfo(APP_NAME, "Çalışan bir test var; manuel işlem için bitmesini bekleyin veya durdurun.")
            return None
        if self.man_busy:
            return None
        try:
            ls = self.settings()
        except ValueError as e:
            messagebox.showwarning(APP_NAME, str(e))
            return None
        sim = self.get_sim(ls) if ls.transport == "sim" else None
        fc = 4 if self.o["read_fc"].get().startswith("FC04") else 3
        off = int(self.o["address_offset"].get() or 0)
        wob = self.o["word_order_big"].get()
        return ls, sim, fc, off, wob

    def _run_manual(self, fn):
        ctx = self._manual_ctx()
        if ctx is None:
            return False
        self.man_busy = True
        ls, sim = ctx[0], ctx[1]

        def work():
            try:
                c = ModbusClient(ls, sim_device=sim).open()
                try:
                    fn(c, *ctx[2:])
                finally:
                    c.close()
            except Exception as e:
                msg = f"Port açılamadı / bağlantı hatası: {e}"
                self.after(0, lambda: messagebox.showerror(APP_NAME, msg))
                self.after(0, lambda: self.v_mpoll.set(False))
            finally:
                self.man_busy = False
        threading.Thread(target=work, daemon=True).start()
        return True

    def _post(self, fn, *a, **k):
        self.after(0, lambda: fn(*a, **k))

    def manual_read(self, selected=True, quiet=False):
        if not self.regs:
            if not quiet:
                messagebox.showinfo(APP_NAME, "Önce tablo yükleyin.")
            return
        if selected:
            regs = [r for r in (self._reg(i) for i in self.tv_man.selection()) if r]
            if not regs:
                if not quiet:
                    messagebox.showinfo(APP_NAME, "Listeden register seçin (Ctrl/Shift ile çoklu).")
                return
        else:
            vis = set(self.tv_man.get_children())
            regs = [r for r in self.regs if str(r.address) in vis]
        regs = [r for r in regs if r.readable]

        def fn(c, fc, off, wob):
            groups = make_groups(regs, lambda r: True, 100)
            for g in groups:
                fcg = fc if g[0].access == "R" else 3
                same = all((fc if r.access == "R" else 3) == fcg for r in g)
                n = sum(r.words for r in g)
                try:
                    if not same or len(g) == 1:
                        raise ModbusError("tek tek")
                    words = c.read_registers(g[0].address - off, n, function=fcg)
                    o = 0
                    for r in g:
                        w = words[o:o + r.words]
                        o += r.words
                        self._post(self._man_ok_read, r, w, wob, f"Blok okuma FC{fcg:02d} ({g[0].address}-{g[-1].address})")
                except ModbusError:
                    for r in g:
                        f1 = fc if r.access == "R" else 3
                        try:
                            w = c.read_registers(r.address - off, r.words, function=f1)
                            self._post(self._man_ok_read, r, w, wob, f"Okuma FC{f1:02d}")
                        except ModbusError as e:
                            self._post(self._man_set, r, status=f"Okuma hatası: {e}", tag="fail")
        self._run_manual(fn)

    def _man_ok_read(self, r, w, wob, how):
        v = decode_value(r, w, wob)
        tag = "ok"
        note = how
        if (r.min is not None and v < r.min) or (r.max is not None and v > r.max):
            tag = "warn"
            note += " - Min/Max dışında"
        self._man_set(r, value=fmt_value(v), raw=" ".join(f"0x{x:04X}" for x in w), status=note, tag=tag)

    def _parse_value(self, txt):
        t = txt.strip().replace(",", ".")
        if t.lower().startswith("0x"):
            return int(t, 16)
        f = float(t)
        return int(f) if f.is_integer() else f

    def manual_write(self):
        regs = [r for r in (self._reg(i) for i in self.tv_man.selection()) if r]
        if not regs:
            messagebox.showinfo(APP_NAME, "Yazmak için listeden register seçin.")
            return
        try:
            v = self._parse_value(self.v_mval.get())
        except ValueError:
            messagebox.showwarning(APP_NAME, "Değer sayı olmalı (örn. 12, -5, 0x1A)")
            return
        ro = [r for r in regs if r.access == "R"]
        if ro and not messagebox.askyesno(APP_NAME, f"{len(ro)} register tabloda salt okunur (R). Yine de yazılsın mı?"):
            return
        out = [r for r in regs if (r.min is not None and v < r.min) or (r.max is not None and v > r.max)]
        if out and not messagebox.askyesno(APP_NAME, "Değer tablodaki Min/Max dışında. Yine de yazılsın mı?"):
            return
        use16 = self.v_mfc16.get()

        def fn(c, fc, off, wob):
            for r in regs:
                words = encode_value(r, v, wob)
                try:
                    if r.words == 1 and not use16:
                        c.write_register(r.address - off, words[0])
                        how = "FC06"
                    else:
                        c.write_registers(r.address - off, words)
                        how = "FC16"
                except ModbusError as e:
                    self._post(self._man_set, r, status=f"Yazma {fmt_value(v)} reddedildi: {e}", tag="fail")
                    continue
                try:
                    back = c.read_registers(r.address - off, r.words, function=fc if r.access == "R" else 3)
                except ModbusError as e:
                    self._post(self._man_set, r, status=f"{how} {fmt_value(v)} yazıldı, geri okuma hatası: {e}", tag="warn")
                    continue
                bv = decode_value(r, back, wob)
                ok = back == words
                self._post(self._man_set, r, value=fmt_value(bv), raw=" ".join(f"0x{x:04X}" for x in back),
                           status=(f"{how} {fmt_value(v)} yazıldı, geri okundu: " + ("doğrulandı" if ok else f"FARKLI ({fmt_value(bv)})")),
                           tag="ok" if ok else "fail")
        self._run_manual(fn)

    def raw_read(self):
        try:
            a = int(self.v_raddr.get().strip(), 0)
            n = int(self.v_rcount.get())
        except ValueError:
            messagebox.showwarning(APP_NAME, "Adres ve adet sayı olmalı")
            return
        fc = 4 if self.v_rfc.get() == "FC04" else 3

        def fn(c, _fc, off, wob):
            try:
                vals = c.read_registers(a - off, n, function=fc)
                txt = ", ".join(f"{a + i}={v} (0x{v:04X})" for i, v in enumerate(vals))
                self._post(self._log_manual, f"FC{fc:02d} {a}..{a + n - 1}: {txt}")
            except ModbusError as e:
                self._post(self._log_manual, f"FC{fc:02d} {a}..{a + n - 1}: HATA {e}")
        self._run_manual(fn)

    def raw_write(self):
        try:
            a = int(self.v_raddr.get().strip(), 0)
            vals = [int(float(x), 0) if not x.strip().lower().startswith("0x") else int(x, 16)
                    for x in self.v_rvals.get().replace(";", ",").split(",") if x.strip()]
        except ValueError:
            messagebox.showwarning(APP_NAME, "Adres ve değerler sayı olmalı (örn. 1, 2, 0x10)")
            return
        if not vals:
            messagebox.showwarning(APP_NAME, "Yazılacak değer girin")
            return

        def fn(c, fc, off, wob):
            try:
                if len(vals) == 1:
                    c.write_register(a - off, vals[0] & 0xFFFF)
                else:
                    c.write_registers(a - off, [v & 0xFFFF for v in vals])
                back = c.read_registers(a - off, len(vals), function=3)
                ok = back == [v & 0xFFFF for v in vals]
                self._post(self._log_manual, f"Yazıldı {a}: {vals} → geri okunan {back} " + ("(doğrulandı)" if ok else "(FARKLI)"))
            except ModbusError as e:
                self._post(self._log_manual, f"Yazma {a} {vals}: HATA {e}")
        self._run_manual(fn)

    def _log_manual(self, msg):
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", time.strftime("%H:%M:%S ") + "[Manuel] " + msg + "\n")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def manual_poll_toggle(self):
        if self._mpoll_after:
            self.after_cancel(self._mpoll_after)
            self._mpoll_after = None
        if self.v_mpoll.get():
            self._manual_poll_tick()

    def _manual_poll_tick(self):
        if not self.v_mpoll.get():
            return
        if not self.man_busy and not self.busy():
            sel = [r for r in (self._reg(i) for i in self.tv_man.selection()) if r]
            self.manual_read(selected=bool(sel), quiet=True)
        try:
            ms = max(200, int(self.v_mpoll_ms.get()))
        except ValueError:
            ms = 1000
        self._mpoll_after = self.after(ms, self._manual_poll_tick)

    def _build_results_tab(self):
        t = ttk.Frame(self.nb, padding=6)
        self.tab_results = t
        self.nb.add(t, text="  Sonuçlar (canlı)  ")
        b = ttk.Frame(t)
        b.pack(fill="x", pady=(0, 4))
        self.v_resfilter = tk.StringVar(value="Tümü")
        ttk.Label(b, text="Göster:").pack(side="left")
        cb = ttk.Combobox(b, textvariable=self.v_resfilter, values=["Tümü", "Sadece HATA", "HATA + UYARI"], state="readonly", width=14)
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self.render_results())
        self.v_follow = tk.BooleanVar(value=True)
        ttk.Checkbutton(b, text="Canlı takip (test edilen registera git)", variable=self.v_follow).pack(side="left", padx=8)
        self.lbl_ressum = ttk.Label(b, text="", font=("Segoe UI", 9, "bold"))
        self.lbl_ressum.pack(side="left", padx=10)
        self.res_frame = ttk.Frame(t)
        self.res_frame.pack(fill="both", expand=True)
        self.tv_res = None
        self.last_result = None

    def _tree(self, parent, cols, heads, widths):
        fr = ttk.Frame(parent)
        fr.pack(fill="both", expand=True)
        tv = ttk.Treeview(fr, columns=cols, show="headings", selectmode="extended")
        for c, h, w in zip(cols, heads, widths):
            tv.heading(c, text=h)
            tv.column(c, width=w, stretch=c in ("name", "remarks", "notes", "note"), anchor="w")
        vs = ttk.Scrollbar(fr, orient="vertical", command=tv.yview)
        hs = ttk.Scrollbar(fr, orient="horizontal", command=tv.xview)
        tv.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        tv.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        fr.rowconfigure(0, weight=1)
        fr.columnconfigure(0, weight=1)
        tv.tag_configure("ok", background="#e3f5e8")
        tv.tag_configure("fail", background="#fde0de")
        tv.tag_configure("warn", background="#fff3c4")
        tv.tag_configure("skip", foreground="#888")
        tv.tag_configure("sec", background="#dfe6f3", font=("Segoe UI", 9, "bold"))
        return tv

    def _build_status(self, parent):
        f = ttk.LabelFrame(parent, text=" Durum ", padding=6)
        f.pack(fill="both", expand=True, pady=(6, 0))
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.pb = ttk.Progressbar(top, maximum=1000)
        self.pb.pack(side="left", fill="x", expand=True)
        self.btn_stop = ttk.Button(top, text="Durdur", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        info = ttk.Frame(f)
        info.pack(fill="x", pady=4)
        self.lbl_phase = ttk.Label(info, text="Hazır", font=("Segoe UI", 9, "bold"))
        self.lbl_phase.pack(side="left")
        self.lbl_sum = ttk.Label(info, text="")
        self.lbl_sum.pack(side="left", padx=12)
        rb = ttk.Frame(info)
        rb.pack(side="right")
        self.btn_html = ttk.Button(rb, text="Raporu aç (HTML)", state="disabled", command=lambda: self.open_report("html"))
        self.btn_xlsx = ttk.Button(rb, text="Excel'i aç", state="disabled", command=lambda: self.open_report("xlsx"))
        self.btn_saveas = ttk.Button(rb, text="Farklı kaydet...", state="disabled", command=self.save_report_as)
        self.btn_folder = ttk.Button(rb, text="Rapor klasörü", command=lambda: open_path(report_dir()))
        for w in (self.btn_html, self.btn_xlsx, self.btn_saveas, self.btn_folder):
            w.pack(side="left", padx=2)
        lf = ttk.Frame(f)
        lf.pack(fill="both", expand=True)
        self.txt_log = tk.Text(lf, height=10, wrap="none", font=("Consolas", 9), background="#10172a", foreground="#d5deee",
                               insertbackground="#fff", relief="flat")
        vs = ttk.Scrollbar(lf, orient="vertical", command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=vs.set, state="disabled")
        self.txt_log.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")

    # ------------------------------------------------------------- helpers
    def transport(self) -> str:
        return dict(TRANSPORTS).get(self.v_transport.get(), "rtu")

    def update_conn_ui(self):
        t = self.transport()
        tcp = t in ("tcp", "rtu_over_tcp")
        self.lbl_port.configure(text="Host:Port" if tcp else ("Port" if t != "sim" else "Port (gerekmez)"))
        st = "disabled" if t == "sim" else "normal"
        self.cb_port.configure(state=st)
        self.btn_ports.configure(state="disabled" if tcp or t == "sim" else "normal")
        for w in (self.w_baud, self.w_parity, self.w_bytesize, self.w_stop):
            w.configure(state="disabled" if tcp else ("normal" if w is self.w_baud else "readonly"))
        self.chk_sim.configure(state="normal" if t == "sim" else "disabled")
        if tcp and not self.v_port.get().count(":"):
            self.v_port.set("192.168.1.10:502")

    def refresh_ports(self):
        ports = list_serial_ports()
        self.port_map = {f"{d} — {desc}": d for d, desc in ports}
        self.cb_port.configure(values=list(self.port_map.keys()))
        if not self.v_port.get() and ports:
            self.v_port.set(f"{ports[0][0]} — {ports[0][1]}")

    def port_value(self) -> str:
        v = self.v_port.get().strip()
        if v in getattr(self, "port_map", {}):
            return self.port_map[v]
        return v.split(" — ")[0].strip()

    def _int(self, var, name, lo=None, hi=None) -> int:
        try:
            v = int(float(str(var.get()).strip()))
        except ValueError:
            raise ValueError(f"'{name}' sayı olmalı")
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            raise ValueError(f"'{name}' {lo}..{hi} aralığında olmalı")
        return v

    def settings(self) -> LineSettings:
        t = self.transport()
        port = "SIM" if t == "sim" else self.port_value()
        if not port:
            raise ValueError("Port seçin veya yazın")
        return LineSettings(
            transport=t, port=port, slave=self._int(self.v_slave, "Slave adresi", 0, 247),
            baudrate=self._int(self.v_baud, "Baud rate", 300, 1000000), parity=dict(PARITIES)[self.v_parity.get()],
            bytesize=int(self.v_bytesize.get()), stopbits=float(self.v_stopbits.get()),
            timeout=self._int(self.v_timeout, "Zaman aşımı", 10, 60000) / 1000.0,
            retries=self._int(self.v_retries, "Tekrar sayısı", 0, 10),
            inter_request_delay=self._int(self.v_delay, "İstekler arası bekleme", 0, 10000) / 1000.0)

    def get_sim(self, ls: LineSettings):
        faults = self.v_simfaults.get()
        key = (id(self.regs), faults)
        if self.sim is None or self.sim_key != key:
            f = None
            if faults:
                ro = next((r.address for r in self.regs if r.access == "R"), None)
                rw = [r.address for r in self.regs if r.access == "RW" and not r.comm_param]
                f = {"no_store": rw[1:2], "accept_ro": [ro] if ro is not None else []}
            self.sim = SimDevice(self.regs, ls, f)
            self.sim_key = key
        return self.sim

    def busy(self) -> bool:
        return self.job is not None and not self.job.done

    # ------------------------------------------------------------- actions
    def probe(self):
        try:
            ls = self.settings()
        except ValueError as e:
            messagebox.showwarning(APP_NAME, str(e))
            return
        addr = self.v_probe.get().strip()
        if not addr:
            r = next((r for r in self.regs if r.readable), None)
            if r is None:
                messagebox.showinfo(APP_NAME, "Önce tablo yükleyin veya register adresi yazın.")
                return
            addr = r.address
        try:
            addr = int(addr)
        except ValueError:
            messagebox.showwarning(APP_NAME, "Register adresi sayı olmalı")
            return
        sim = self.get_sim(ls) if ls.transport == "sim" else None
        if ls.transport == "sim" and sim is None:
            return
        fc = 4 if self.o["read_fc"].get().startswith("FC04") else 3
        off = int(self.o["address_offset"].get() or 0)
        self.lbl_probe.configure(text="Deneniyor...", foreground="#555")

        def work():
            try:
                c = ModbusClient(ls, sim_device=sim).open()
                try:
                    vals = c.read_registers(addr - off, 1, function=fc)
                finally:
                    c.close()
                msg, col = f"✔ {ls.describe()}: register {addr} = {vals[0]} (0x{vals[0]:04X})", "#1e7d3a"
            except ModbusError as e:
                msg, col = f"✖ {ls.describe()}: {e}", "#b42318"
            except Exception as e:
                msg, col = f"✖ Port açılamadı: {e}", "#b42318"
            self.after(0, lambda: self.lbl_probe.configure(text=msg, foreground=col))
        threading.Thread(target=work, daemon=True).start()

    def load_table_dialog(self):
        p = filedialog.askopenfilename(title="Register tablosu seç", filetypes=[
            ("Register tablosu", "*.pdf *.xlsx *.xlsm *.csv *.txt *.json"), ("Tüm dosyalar", "*.*")])
        if p:
            self.load_table_file(p)

    def load_table_file(self, p):
        try:
            with open(p, "rb") as fh:
                regs, warns = load_table(p, fh.read())
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Tablo okunamadı:\n{e}")
            return
        if not regs:
            messagebox.showerror(APP_NAME, "Tabloda register bulunamadı")
            return
        self.regs, self.warnings, self.table_name = regs, warns, os.path.basename(p)
        self.sim = None
        nR = sum(1 for r in regs if r.access == "R")
        nRW = sum(1 for r in regs if r.access == "RW")
        nW = sum(1 for r in regs if r.access == "W")
        nS = sum(1 for r in regs if r.skip)
        self.lbl_table.configure(text=f"✔ {self.table_name}\n{len(regs)} register  (R: {nR}, R/W: {nRW}, W: {nW}, atlanan: {nS})",
                                 foreground="#1e7d3a")
        self.txt_warn.configure(state="normal")
        self.txt_warn.delete("1.0", "end")
        self.txt_warn.insert("end", "\n".join(warns) if warns else "Uyarı yok.")
        self.txt_warn.configure(state="disabled")
        self.params = default_comm_config(regs)
        self.render_params()
        self.render_regs()
        self.man_state = {}
        self.render_manual()
        self.cfg_last_table = p
        self.save_cfg()

    def export_csv(self):
        if not self.regs:
            messagebox.showinfo(APP_NAME, "Önce tablo yükleyin.")
            return
        p = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="register_tablosu.csv",
                                         filetypes=[("CSV", "*.csv")])
        if p:
            with open(p, "wb") as fh:
                fh.write(save_csv(self.regs))

    def render_regs(self):
        tv = self.tv_regs
        tv.delete(*tv.get_children())
        q = self.v_filter.get().lower().strip()
        sec = None
        for r in self.regs:
            txt = f"{r.address} {r.hex} {r.name} {r.section} {r.remarks}".lower()
            if q and q not in txt:
                continue
            if r.section != sec:
                sec = r.section
                tv.insert("", "end", iid=f"sec_{r.address}", values=("", "", "", "", sec or "", "", "", "", "", ""), tags=("sec",))
            tags = []
            if r.skip:
                tags.append("skip")
            if r.comm_param:
                tags.append("comm")
            fv = lambda v: "" if v is None else (int(v) if float(v).is_integer() else v)
            name = r.name + (f"  [{KIND_LABEL[r.comm_param]}]" if r.comm_param else "")
            tv.insert("", "end", iid=str(r.address), tags=tags, values=(
                "✔" if r.skip else "", r.address, r.hex, r.fmt, name, {"RW": "R/W"}.get(r.access, r.access or "-"),
                fv(r.min), fv(r.max), r.section, r.remarks))

    def _reg(self, iid):
        if iid.startswith("sec_"):
            return None
        a = int(iid)
        return next((r for r in self.regs if r.address == a), None)

    def toggle_skip(self, ev):
        iid = self.tv_regs.identify_row(ev.y)
        r = self._reg(iid) if iid else None
        if r and r.access:
            r.skip = not r.skip
            r.skip_reason = "Kullanıcı tarafından atlandı" if r.skip else ""
            self.render_regs()
            self.tv_regs.see(iid)

    def set_skip(self, val):
        for iid in self.tv_regs.selection():
            r = self._reg(iid)
            if r and r.access:
                r.skip = val
                r.skip_reason = "Kullanıcı tarafından atlandı" if val else ""
        self.render_regs()

    # params editor
    def render_params(self):
        for w in self.param_box.winfo_children():
            w.destroy()
        self.param_rows = []
        if not self.params:
            ttk.Label(self.param_box, text="Haberleşme ayar registerı bulunamadı (tablo yükleyin veya parametre ekleyin).",
                      foreground="#777").pack(anchor="w", pady=4)
            return
        for i, p in enumerate(self.params):
            fr = ttk.Frame(self.param_box)
            fr.pack(fill="x", pady=1)
            ve = tk.BooleanVar(value=p.get("enabled", True))
            vr = tk.StringVar(value=str(p["register"]))
            vv = tk.StringVar(value=", ".join(f"{v['raw']}={v['setting']}" for v in p["values"]))
            ttk.Checkbutton(fr, variable=ve, width=5).pack(side="left", padx=(6, 2))
            ttk.Label(fr, text=KIND_LABEL[p["kind"]], width=14).pack(side="left", padx=2)
            ttk.Entry(fr, textvariable=vr, width=10).pack(side="left", padx=2)
            ttk.Entry(fr, textvariable=vv, width=50).pack(side="left", padx=2, fill="x", expand=True)
            ttk.Label(fr, text=p.get("name", ""), foreground="#777", width=22).pack(side="left", padx=4)
            ttk.Button(fr, text="Sil", width=4, command=lambda i=i: self.del_param(i)).pack(side="left")
            self.param_rows.append((p["kind"], ve, vr, vv, p.get("name", "")))

    def collect_params(self):
        out = []
        for kind, ve, vr, vv, name in self.param_rows:
            vals = []
            for part in [x.strip() for x in vv.get().replace(";", ",").split(",") if x.strip()]:
                a, _, b = part.partition("=")
                b = b.strip() or a.strip()
                try:
                    raw = int(a.strip())
                except ValueError:
                    raise ValueError(f"{KIND_LABEL[kind]}: '{part}' geçersiz (ham=ayar biçiminde olmalı)")
                if kind == "parity":
                    st = b.upper()[:1]
                    if st not in "NEO":
                        raise ValueError(f"Parity değeri N, E veya O olmalı: '{part}'")
                elif kind == "stopbits":
                    st = float(b.replace(",", "."))
                else:
                    st = int(b)
                vals.append({"raw": raw, "setting": st})
            try:
                reg = int(vr.get())
            except ValueError:
                raise ValueError(f"{KIND_LABEL[kind]}: register adresi sayı olmalı")
            out.append({"kind": kind, "register": reg, "values": vals, "enabled": ve.get(), "name": name})
        return out

    def add_param(self):
        try:
            self.params = self.collect_params()
        except ValueError:
            pass
        kind = {v: k for k, v in KIND_LABEL.items()}[self.v_newkind.get()]
        self.params.append({"kind": kind, "register": 0, "values": [], "enabled": True, "name": ""})
        self.render_params()

    def del_param(self, i):
        try:
            self.params = self.collect_params()
        except ValueError:
            pass
        del self.params[i]
        self.render_params()

    # jobs
    def _begin(self, job: Job):
        self.job = job
        self.log_pos = 0
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.configure(state="disabled")
        for b in (self.btn_html, self.btn_xlsx, self.btn_saveas):
            b.configure(state="disabled")
        self.btn_test.configure(state="disabled")
        self.btn_scan.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.lbl_sum.configure(text="")
        self.last_result = None
        self.tv_res = None
        self._last_live = 0.0
        self.nb.select(self.tab_results)
        self.save_cfg()

    def start_test(self):
        if self.busy():
            return
        if not self.regs:
            messagebox.showinfo(APP_NAME, "Önce register tablosu yükleyin.")
            return
        try:
            ls = self.settings()
            o = self.o
            opts = TestOptions(
                read_fc=4 if o["read_fc"].get().startswith("FC04") else 3,
                single_write=o["single_write"].get(), test_boundaries=o["test_boundaries"].get(),
                block_read=o["block_read"].get(), block_write=o["block_write"].get(),
                block_size=self._int(o["block_size"], "Blok boyutu", 2, 123), ro_protection=o["ro_protection"].get(),
                range_protection=o["range_protection"].get(), write_only_regs=o["write_only_regs"].get(),
                restore=o["restore"].get(), skip_comm=o["skip_comm"].get(),
                settle_ms=self._int(o["settle_ms"], "Yazma sonrası bekleme", 0, 60000),
                word_order_big=o["word_order_big"].get(), address_offset=self._int(o["address_offset"], "Adres ofseti"))
        except ValueError as e:
            messagebox.showwarning(APP_NAME, str(e))
            return
        if not opts.restore and (opts.single_write or opts.block_write):
            if not messagebox.askyesno(APP_NAME, "Orijinal değerleri geri yazma kapalı. Cihazdaki değerler test değerlerinde kalacak. Devam edilsin mi?"):
                return
        sim = self.get_sim(ls) if ls.transport == "sim" else None
        regs = self.regs
        title = self.v_title1.get().strip() or f"Modbus Register Test Raporu - {self.table_name}"
        job = Job("test")

        def work(j):
            try:
                run_register_test(j, regs, ls, opts, sim)
            finally:
                r = j.result
                if r is not None and r.results:
                    j.reports = write_reports("test", r.to_dict(), report_dir(), f"register_test_{j.id}",
                                              [x.to_dict() for x in regs], title)
                    j.log(f"Rapor kaydedildi: {j.reports['xlsx']}")
        self._begin(job)
        job.start(work)

    def start_scan(self):
        if self.busy():
            return
        if not self.regs:
            messagebox.showinfo(APP_NAME, "Önce register tablosu yükleyin.")
            return
        try:
            ls = self.settings()
            params = self.collect_params()
            s = self.s
            opts = ScanOptions(
                mode="matrix" if s["mode"].get().startswith("Tüm") else "sequential",
                settle_ms=self._int(s["settle_ms"], "Ayar sonrası bekleme", 0, 120000),
                verify_retries=self._int(s["verify_retries"], "Doğrulama deneme sayısı", 1, 50),
                functional_check=s["functional_check"].get(), check_old_silent=s["check_old_silent"].get(),
                recovery_search=s["recovery_search"].get(), restore=s["restore"].get(),
                parity_none_2stop=s["parity_none_2stop"].get(), apply_register=s["apply_register"].get().strip(),
                address_offset=self._int(self.o["address_offset"], "Adres ofseti"),
                read_fc=4 if self.o["read_fc"].get().startswith("FC04") else 3)
        except ValueError as e:
            messagebox.showwarning(APP_NAME, str(e))
            return
        self.params = params
        en = [p for p in params if p["enabled"] and p["values"]]
        if not en:
            messagebox.showwarning(APP_NAME, "Taranacak (işaretli ve değeri girilmiş) parametre yok.")
            return
        if ls.transport in ("tcp", "rtu_over_tcp") and any(p["kind"] != "address" for p in en):
            if not messagebox.askyesno(APP_NAME, "TCP bağlantısında baud/parity değişikliği doğrulanamaz. Yine de devam edilsin mi?"):
                return
        if not messagebox.askyesno(APP_NAME, "Ayar tarama modu cihazın haberleşme ayarlarını (adres/baud/parity...) değiştirecek.\nDevam edilsin mi?"):
            return
        sim = self.get_sim(ls) if ls.transport == "sim" else None
        regs = self.regs
        title = self.v_title2.get().strip() or f"Modbus Haberleşme Ayarları Tarama Raporu - {self.table_name}"
        job = Job("scan")

        def work(j):
            try:
                run_comm_scan(j, regs, ls, en, opts, sim)
            finally:
                if j.result is not None:
                    j.reports = write_reports("scan", j.result.to_dict(), report_dir(), f"ayar_tarama_{j.id}", None, title)
                    j.log(f"Rapor kaydedildi: {j.reports['xlsx']}")
        self._begin(job)
        job.start(work)

    def stop(self):
        if self.job:
            self.job.cancel()
            self.lbl_phase.configure(text="Durduruluyor...")

    def poll(self):
        j = self.job
        if j is not None:
            new = j.logs[self.log_pos:]
            if new:
                self.log_pos += len(new)
                self.txt_log.configure(state="normal")
                at_end = self.txt_log.yview()[1] > 0.98
                self.txt_log.insert("end", "\n".join(new) + "\n")
                n = int(self.txt_log.index("end-1c").split(".")[0])
                if n > 5000:
                    self.txt_log.delete("1.0", f"{n - 4000}.0")
                if at_end:
                    self.txt_log.see("end")
                self.txt_log.configure(state="disabled")
            self.pb["value"] = int(j.progress * 1000)
            res = j.result
            sm = getattr(res, "summary", None) if res is not None else None
            if sm:
                if j.kind == "test" and "overall" in sm:
                    s = sm["overall"]
                    self.lbl_sum.configure(text=f"Toplam {sm.get('total', 0)}   OK: {s['OK']}   UYARI: {s['UYARI']}   HATA: {s['HATA']}   ATLANDI: {s['ATLANDI']}")
                elif j.kind == "scan" and "steps" in sm:
                    self.lbl_sum.configure(text=f"{sm['steps']} adım   OK: {sm['OK']}   UYARI: {sm['UYARI']}   HATA: {sm['HATA']}")
            if not j.done:
                self.lbl_phase.configure(text=j.phase or "Çalışıyor...")
                if time.time() - getattr(self, "_last_live", 0) > 0.7:
                    self._last_live = time.time()
                    self.live_update(j)
            elif getattr(j, "_handled", False) is False:
                j._handled = True
                self.on_job_done(j)
        self.after(250, self.poll)

    def on_job_done(self, j: Job):
        self.btn_test.configure(state="normal")
        self.btn_scan.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        res = j.result
        aborted = getattr(res, "aborted", "") if res is not None else ""
        if j.error or aborted:
            self.lbl_phase.configure(text="Hata / durduruldu: " + (aborted or j.error or ""), foreground="#b42318")
        else:
            self.lbl_phase.configure(text="Tamamlandı", foreground="#1e7d3a")
        if j.reports:
            for b in (self.btn_html, self.btn_xlsx, self.btn_saveas):
                b.configure(state="normal")
        if res is not None:
            self.last_result = (j.kind, res)
            self.render_results()
            self.nb.select(self.tab_results)
        if j.kind == "scan" and res is not None and getattr(res, "final_settings", ""):
            self.apply_final(res)
        self.bell()

    def apply_final(self, run):
        """Tarama sonrasi baglanti ayarlarini cihazin son ayarina cek."""
        import re
        m = re.match(r"adres=(\d+) (\d+) (\d)([NEO])([\d.]+)", run.final_settings or "")
        if not m:
            return
        self.v_slave.set(m.group(1))
        self.v_baud.set(m.group(2))
        self.v_bytesize.set(m.group(3))
        self.v_parity.set({"N": "None", "E": "Even", "O": "Odd"}[m.group(4)])
        sb = float(m.group(5))
        self.v_stopbits.set(str(int(sb)) if sb.is_integer() else str(sb))

    # ---- sonuc tablosu (test sirasinda canli guncellenir)
    def _res_show(self, st):
        flt = self.v_resfilter.get()
        if flt == "Sadece HATA":
            return st == "HATA"
        if flt == "HATA + UYARI":
            return st in ("HATA", "UYARI")
        return True

    @staticmethod
    def _fv(v):
        return "" if v is None else (int(v) if float(v).is_integer() else v)

    def _test_row(self, r, rr):
        return (r["address"], r["name"], r.get("access_label", ""), self._fv(r.get("min")), self._fv(r.get("max")),
                rr.value, rr.read, rr.written, rr.readback, rr.write, rr.block_read, rr.block_write, rr.ro_protect,
                rr.range_protect, rr.overall, " | ".join(rr.notes))

    def _scan_row(self, st):
        return (st.no, st.purpose, st.param, st.register, st.raw, st.setting, st.from_settings, st.to_settings,
                st.write_resp, st.verify, st.old_silent, st.functional, st.status, st.duration, st.note)

    def render_results(self):
        if not self.last_result:
            return
        kind, res = self.last_result
        for w in self.res_frame.winfo_children():
            w.destroy()
        self.res_cache = {}
        flt = self.v_resfilter.get()
        if kind == "test":
            cols = ("address", "name", "access", "min", "max", "value", "read", "written", "readback", "write",
                    "block_read", "block_write", "ro_protect", "range_protect", "overall", "notes")
            heads = ("Address", "Designation", "R/W", "Min", "Max", "Okunan", "Okuma", "Yazılan", "Geri okunan", "Yazma",
                     "Blok okuma", "Blok yazma", "R koruma", "Aralık", "SONUÇ", "Açıklama")
            widths = (65, 190, 45, 50, 50, 70, 60, 80, 80, 60, 75, 75, 65, 60, 70, 400)
            tv = self._tree(self.res_frame, cols, heads, widths)
            tv.tag_configure("cur", background="#cfe2ff")
            sec = None
            for r in res.registers:
                rr = res.results.get(r["address"])
                if rr is None or not self._res_show(rr.overall):
                    continue
                if r.get("section") != sec and flt == "Tümü":
                    sec = r.get("section")
                    tv.insert("", "end", values=("", sec or ""), tags=("sec",))
                row = self._test_row(r, rr)
                tv.insert("", "end", iid=str(r["address"]), tags=(STATUS_TAG.get(rr.overall, ""),), values=row)
                self.res_cache[r["address"]] = row
        else:
            cols = ("no", "purpose", "param", "register", "raw", "setting", "from", "to", "write", "verify", "old",
                    "func", "status", "dur", "note")
            heads = ("#", "Amaç", "Parametre", "Register", "Ham", "Ayar", "Önceki hat", "Yeni hat", "Yazma yanıtı",
                     "Doğrulama", "Eski sessiz", "Fonksiyon", "SONUÇ", "Süre", "Açıklama")
            widths = (35, 80, 100, 70, 50, 70, 120, 120, 150, 75, 75, 75, 65, 50, 400)
            tv = self._tree(self.res_frame, cols, heads, widths)
            for st in list(res.steps):
                row = self._scan_row(st)
                self.res_cache[st.no] = row
                if self._res_show(st.status):
                    tv.insert("", "end", iid=f"s{st.no}", tags=(STATUS_TAG.get(st.status, ""),), values=row)
        self.tv_res = tv
        self.update_res_summary()

    def update_res_summary(self):
        kind, res = self.last_result
        if kind == "test":
            s = summarize(res.results)["overall"] if res.results else {}
            done = sum(1 for x in res.results.values() if x.overall != "-")
            self.lbl_ressum.configure(text=f"Register testi — {done}/{len(res.results)} register   OK: {s.get('OK', 0)}  "
                                           f"UYARI: {s.get('UYARI', 0)}  HATA: {s.get('HATA', 0)}  ATLANDI: {s.get('ATLANDI', 0)}")
        else:
            steps = list(res.steps)
            c = {k: sum(1 for x in steps if x.status == k) for k in ("OK", "UYARI", "HATA")}
            self.lbl_ressum.configure(text=f"Ayar tarama — {len(steps)} adım  OK: {c['OK']}  UYARI: {c['UYARI']}  HATA: {c['HATA']}"
                                           + (f"   Son ayar: {res.final_settings}" if res.final_settings else ""))

    def live_update(self, job):
        """Calisan testin sonuclarini tabloya yansit (sadece degisen satirlar)."""
        res = job.result
        if res is None:
            return
        if job.kind == "test" and not res.results:
            return
        if not self.last_result or self.last_result[1] is not res or self.tv_res is None:
            self.last_result = (job.kind, res)
            self.render_results()
            return
        tv = self.tv_res
        try:
            if job.kind == "test":
                regs = {r["address"]: r for r in res.registers}
                for addr, rr in list(res.results.items()):
                    row = self._test_row(regs[addr], rr)
                    if self.res_cache.get(addr) == row:
                        continue
                    self.res_cache[addr] = row
                    iid = str(addr)
                    if tv.exists(iid):
                        if self._res_show(rr.overall):
                            tv.item(iid, values=row, tags=(STATUS_TAG.get(rr.overall, ""),))
                        else:
                            tv.delete(iid)
                    elif self._res_show(rr.overall):
                        tv.insert("", "end", iid=iid, values=row, tags=(STATUS_TAG.get(rr.overall, ""),))
                if self.v_follow.get() and job.current is not None and tv.exists(str(job.current)):
                    iid = str(job.current)
                    tv.see(iid)
                    tv.selection_set(iid)
            else:
                for st in list(res.steps):
                    row = self._scan_row(st)
                    if self.res_cache.get(st.no) == row:
                        continue
                    self.res_cache[st.no] = row
                    iid = f"s{st.no}"
                    if tv.exists(iid):
                        tv.item(iid, values=row, tags=(STATUS_TAG.get(st.status, ""),))
                    elif self._res_show(st.status):
                        tv.insert("", "end", iid=iid, values=row, tags=(STATUS_TAG.get(st.status, ""),))
                        if self.v_follow.get():
                            tv.see(iid)
        except tk.TclError:
            self.render_results()
        self.update_res_summary()

    def open_report(self, ext):
        if self.job and ext in self.job.reports:
            open_path(self.job.reports[ext])

    def save_report_as(self):
        if not (self.job and self.job.reports):
            return
        p = filedialog.asksaveasfilename(defaultextension=".xlsx", initialfile=os.path.basename(self.job.reports["xlsx"]),
                                         filetypes=[("Excel", "*.xlsx"), ("HTML", "*.html"), ("CSV", "*.csv")])
        if not p:
            return
        ext = os.path.splitext(p)[1].lower().lstrip(".") or "xlsx"
        src = self.job.reports.get(ext, self.job.reports["xlsx"])
        import shutil
        shutil.copyfile(src, p)

    # ------------------------------------------------------------- config
    def _vars(self):
        d = {"transport": self.v_transport, "port": self.v_port, "slave": self.v_slave, "baud": self.v_baud,
             "parity": self.v_parity, "bytesize": self.v_bytesize, "stopbits": self.v_stopbits, "timeout": self.v_timeout,
             "retries": self.v_retries, "delay": self.v_delay}
        d.update({"o_" + k: v for k, v in self.o.items()})
        d.update({"s_" + k: v for k, v in self.s.items()})
        return d

    def save_cfg(self):
        try:
            data = {k: v.get() for k, v in self._vars().items()}
            data["last_table"] = getattr(self, "cfg_last_table", "")
            with open(self.cfg_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
        except Exception:
            pass

    def _load_cfg(self):
        try:
            with open(self.cfg_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            data = {}
        for k, v in self._vars().items():
            if k in data:
                try:
                    v.set(data[k])
                except tk.TclError:
                    pass
        self.update_conn_ui()
        lt = data.get("last_table")
        if lt and os.path.exists(lt):
            self.after(100, lambda: self.load_table_file(lt))

    def on_close(self):
        if self.busy():
            if not messagebox.askyesno(APP_NAME, "Çalışan bir test var. Durdurup çıkılsın mı?"):
                return
            self.job.cancel()
            self.job.join(3)
        self.save_cfg()
        self.destroy()


def selftest(out_path: str) -> int:
    """Pencere acmadan simulatorle uctan uca deneme (derleme kontrolu icin)."""
    lines = []
    try:
        p = os.path.join(BASE, "ornek_DTR-20S-TS.pdf")
        with open(p, "rb") as fh:
            regs, _ = load_table(p, fh.read())
        ls = LineSettings(transport="sim", port="SIM", inter_request_delay=0)
        dev = SimDevice(regs, ls)
        j = Job("test")
        run = run_register_test(j, regs, ls, TestOptions(), dev)
        rep = write_reports("test", run.to_dict(), os.path.join(user_dir(), "selftest"), "selftest", [r.to_dict() for r in regs])
        j2 = Job("scan")
        sr = run_comm_scan(j2, regs, ls, default_comm_config(regs), ScanOptions(settle_ms=0), dev)
        lines.append(f"registers={len(regs)} test={run.summary['overall']} scan={sr.summary} report={rep['xlsx']}")
        ok = run.summary["overall"]["HATA"] == 0 and sr.summary["HATA"] == 0 and not sr.aborted
        try:
            import serial.tools.list_ports  # noqa: F401
            lines.append("serial ok")
        except Exception as e:
            ok = False
            lines.append(f"serial FAIL {e}")
        lines.append("SELFTEST " + ("OK" if ok else "FAIL"))
        code = 0 if ok else 1
    except Exception as e:
        import traceback
        lines.append("SELFTEST FAIL " + traceback.format_exc())
        code = 2
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return code


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--selftest":
        sys.exit(selftest(sys.argv[2]))
    app = App()
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        app.after(200, lambda: app.load_table_file(sys.argv[1]))
    app.mainloop()


if __name__ == "__main__":
    main()

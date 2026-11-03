"""Arka plan is (test / tarama) altyapisi."""
from __future__ import annotations

import threading
import time
import traceback
from typing import Callable, List, Optional

OK = "OK"
FAIL = "HATA"
WARN = "UYARI"
SKIP = "ATLANDI"
NA = "-"


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, kind: str):
        self.kind = kind
        self.id = time.strftime("%Y%m%d_%H%M%S")
        self.logs: List[str] = []
        self.progress = 0.0
        self.phase = ""
        self.current = None   # su an test edilen register adresi
        self.done = False
        self.error: Optional[str] = None
        self.cancel_event = threading.Event()
        self.result = None
        self.reports: dict = {}
        self.started = time.time()
        self.finished: Optional[float] = None
        self._thread: Optional[threading.Thread] = None

    def log(self, msg: str):
        line = time.strftime("%H:%M:%S ") + msg
        self.logs.append(line)
        if len(self.logs) > 20000:
            del self.logs[:5000]

    def set_phase(self, phase: str):
        self.phase = phase
        self.log(f"== {phase} ==")

    def check(self):
        if self.cancel_event.is_set():
            raise Cancelled()

    def cancel(self):
        self.cancel_event.set()

    def start(self, fn: Callable[["Job"], None]):
        def run():
            try:
                fn(self)
            except Cancelled:
                self.error = "Kullanıcı tarafından durduruldu"
                self.log("Durduruldu.")
            except Exception as e:  # pragma: no cover
                self.error = f"{type(e).__name__}: {e}"
                self.log("HATA: " + self.error)
                self.log(traceback.format_exc())
            finally:
                self.finished = time.time()
                self.done = True
        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        return self

    def join(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)

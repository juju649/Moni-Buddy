"""Receive finished results from board_processor.py and hand them to the dashboard.

Two ways to connect:
  serial      the SK-AM62B console over the debug micro-USB cable (for example COM11 at 115200)
  simulated   run board_processor.py as a program on THIS laptop (a stand-in for the board)

The link does no signal processing. It only reads the lines  CT1 <crc32> <json>  that the board prints,
checks the checksum, and keeps the newest waveform and numbers.
"""
import json
import subprocess
import sys
import threading
import time
import zlib
from collections import deque
from pathlib import Path

import numpy as np

import pipeline as p

HERE = Path(__file__).resolve().parent
KEEP_S = 40  # seconds of waveform kept


class BoardLink:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = "idle"  # idle, connecting, waiting, receiving, stale, error, stopped
        self.message = "Not started"
        self.kind = None  # "serial" or "simulated"
        self.port = None
        self.frames = 0
        self.bad_lines = 0
        self.last_rx = 0.0
        self.proc_ms = None
        self.src = None
        self.file = None
        self.raw_tail = deque(maxlen=12)
        self._stop = threading.Event()
        self._thread = None
        self._proc = None
        self._reset_data()

    def _reset_data(self):
        self.et, self.ev = deque(maxlen=KEEP_S * 500), deque(maxlen=KEEP_S * 500)
        self.pt, self.pv = deque(maxlen=KEEP_S * 50), deque(maxlen=KEEP_S * 50)
        self.rpk, self.ppk = deque(maxlen=400), deque(maxlen=400)
        self.last = None
        self.first_frame_t = None

    # ------------------------------------------------------------ control
    def start_serial(self, port, baud=115200):
        self.stop()
        self.kind, self.port = "serial", port
        self._launch(self._run_serial, (port, baud))

    def start_simulated(self, csv_path=None, fast=False):
        self.stop()
        self.kind, self.port = "simulated", "this laptop"
        self._launch(self._run_sim, (csv_path, fast))

    def _launch(self, fn, args):
        with self.lock:
            self._reset_data()
            self.frames = self.bad_lines = 0
            self.proc_ms = None
        self._stop.clear()
        self.state, self.message = "connecting", "Opening link"
        self._thread = threading.Thread(target=fn, args=args, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None
        if self.state not in ("idle", "error"):
            self.state, self.message = "stopped", "Stopped"

    # ------------------------------------------------------------ readers
    def _run_serial(self, port, baud):
        try:
            import serial
        except ImportError:
            self.state, self.message = "error", "pyserial is not installed. Run: pip install pyserial"
            return
        try:
            ser = serial.Serial(port, baud, timeout=0.5)
        except Exception as e:
            self.state, self.message = "error", f"Could not open {port}: {e}. Close PuTTY and any other program using it."
            return
        self.state, self.message = "waiting", f"Listening on {port}. Start board_processor.py on the board."
        buf = b""
        try:
            while not self._stop.is_set():
                chunk = ser.read(4096)
                if not chunk:
                    self._check_stale()
                    continue
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self.feed_line(line.decode("utf-8", "replace"))
        except Exception as e:
            self.state, self.message = "error", f"Serial error: {e}"
        finally:
            try:
                ser.close()
            except Exception:
                pass

    def _run_sim(self, csv_path, fast):
        cmd = [sys.executable, "-u", str(HERE / "board_processor.py"), "--loop"]
        if csv_path:
            cmd += ["--csv", str(csv_path)]
        if fast:
            cmd += ["--fast"]
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except Exception as e:
            self.state, self.message = "error", f"Could not start board_processor.py: {e}"
            return
        self.state, self.message = "waiting", "Simulated board started on this laptop"
        for line in self._proc.stdout:
            if self._stop.is_set():
                break
            self.feed_line(line)

    def _check_stale(self):
        if self.state == "receiving" and time.time() - self.last_rx > 5:
            self.state, self.message = "stale", "No data for 5 seconds. Is board_processor.py still running?"

    # ------------------------------------------------------------ parsing
    def feed_line(self, line):
        line = line.strip()
        if not line:
            return
        self.raw_tail.append(line[:160])
        if not line.startswith("CT1 "):
            return
        try:
            _, crc, body = line.split(" ", 2)
            if (zlib.crc32(body.encode()) & 0xFFFFFFFF) != int(crc, 16):
                raise ValueError("checksum")
            f = json.loads(body)
        except Exception:
            self.bad_lines += 1
            return
        with self.lock:
            self._add(f)
        self.frames += 1
        self.last_rx = time.time()
        self.proc_ms = f.get("ms")
        self.src = f.get("src")
        self.file = f.get("file")
        self.state = "receiving"
        self.message = f"Receiving processed results (frame {f.get('seq')}, board time {f.get('ms')} ms per step)"

    def _add(self, f):
        te, tp = f.get("te", 0.0), f.get("tp", 0.0)
        # A new pass of the recording starts a new time segment. Insert a gap so the chart does not join the two.
        if f.get("ecg") and self.et and te - self.et[-1] > 0.05:
            self.et.append(self.et[-1] + 0.001)
            self.ev.append(float("nan"))
        if f.get("ppg") and self.pt and tp - self.pt[-1] > 0.5:
            self.pt.append(self.pt[-1] + 0.001)
            self.pv.append(float("nan"))
        for k, v in enumerate(f.get("ecg", [])):
            self.et.append(te + k / 500.0)
            self.ev.append(v / 1e6)
        for k, v in enumerate(f.get("ppg", [])):
            self.pt.append(tp + k / 50.0)
            self.pv.append(v / 1e6)
        self.rpk.extend(f.get("rp", []))
        self.ppk.extend(f.get("pp", []))
        self.last = f
        if self.first_frame_t is None:
            self.first_frame_t = f.get("t", 0.0)

    def latest_summary(self):
        """One small dict for the backend forwarder and the ESP32 watch. None until a result arrives."""
        with self.lock:
            f = dict(self.last) if self.last else None
        if f is None:
            return None
        hs = f.get("hs")
        valid = hs is not None and float(f.get("sqi", 0)) >= p.SQI_SHOW
        state, risk = ("no_reading", None)
        if valid:
            from pushevents import classify
            state, risk = classify(hs, f.get("cv"))
        return {"seq": f.get("seq"), "t": f.get("t"), "valid": valid, "hr": None if hs is None else int(round(hs)),
                "state": state, "risk": risk, "sqi": f.get("sqi"), "quality": f.get("lab"),
                "rejected": f.get("rej", 0), "file": f.get("file"), "board_ms": f.get("ms"),
                "source": "recording processed on the board" if self.kind == "serial" else "recording processed on a simulated board",
                "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    # ------------------------------------------------------------ for the dashboard
    def window(self, sec):
        """Filtered samples (already processed on the board) for the last `sec` seconds."""
        with self.lock:
            et, ev = np.array(self.et), np.array(self.ev)
            pt, pv = np.array(self.pt), np.array(self.pv)
        if len(et):
            keep = et >= et[-1] - sec
            et, ev = et[keep], ev[keep]
        if len(pt):
            keep = pt >= pt[-1] - sec
            pt, pv = pt[keep], pv[keep]
        return et, ev, pt, pv

    def result(self, sec=12):
        """Build a pipeline.Result from the board's numbers. Nothing is recalculated here."""
        et, ev, pt, pv = self.window(sec)
        with self.lock:
            f = dict(self.last) if self.last else None
            rpk, ppk = np.array(self.rpk), np.array(self.ppk)
        r = p.Result()
        if f is None:
            return r, (et, ev, pt, pv)
        r.t_end = float(f.get("t", 0.0))
        r.contact = True
        r.hr_ecg, r.hr_ppg = f.get("hr"), f.get("hp")
        r.rmssd_ms, r.rr_cv = f.get("rm"), f.get("cv")
        r.sqi, r.sqi_label = float(f.get("sqi", 0.0)), f.get("lab", "NO SIGNAL")
        r.polarity_flipped = bool(f.get("flip"))
        r.rejected = int(f.get("rej", 0))
        r.hr_smooth = f.get("hs")
        r.reasons = list(f.get("why", []))
        r.ecg_filt, r.ppg_filt = ev, pv
        r.ecg_peaks = _to_index(et, rpk)
        r.ppg_peaks = _to_index(pt, ppk)
        return r, (et, ev, pt, pv)


def _to_index(t, times):
    if len(t) == 0 or len(times) == 0:
        return np.array([], dtype=int)
    times = np.unique(times)
    idx = np.searchsorted(t, times)
    ok = (idx > 0) & (idx < len(t))
    idx = idx[ok]
    # snap to the nearest sample
    left, right = np.abs(t[idx - 1] - times[ok]), np.abs(t[idx] - times[ok])
    idx = np.where(left < right, idx - 1, idx)
    tol = np.abs(t[idx] - times[ok]) < 0.02
    return idx[tol].astype(int)

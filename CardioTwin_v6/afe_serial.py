"""Live reader for the TI AFE4950EVM over its USB serial (CDC) ports.

The protocol comes from TI's own Bio-Sensing scripts (BASE_PROFILE1: searchPort.py and
regCapture.py), not from guesswork:

  * The EVM shows up as two USB CDC ports, VID 2047 and PID 0300 (hex).
    Send "C:CDC?\\n" to a port. It answers "CAPTURE" or "REG_EDIT".
  * The CAPTURE port runs at 128000 baud.
  * "C:CAP_START" + 2 hex digits + 8 hex digits starts streaming. "C:CAP_STOP" stops it.
  * Data arrives as text lines. Fields are separated by tabs and the line ends in a tab.
    Field 0 is hex, and (value + 1) tells how many data words follow (low 8 bits only).
    Each data word is 24-bit two's complement hex. Volts = signed code * 1.2 / 2**21.
  * Words arrive in a repeating FIFO order that the GUI stores in a *_FIFO_SEQ.cfg file.
    Your capture has ECG at 500 Hz and PPG (TIA1-3) at 50 Hz, so one cycle is 11 words:
    10 ECG words and 1 PPG word. If you have the FIFO_SEQ.cfg file, load it. If not, this
    module finds the PPG slot by trying all 11 positions and keeping the one that gives
    the cleanest heartbeat.

NOT TESTED ON HARDWARE YET. It is tested against a simulated device that speaks the same
text protocol. The AFE must already hold a working register setup (see README: prime it
once with the TI GUI, then close the GUI so the port is free).
"""
import collections
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import serial
from serial.tools import list_ports

import pipeline as p

VID, PID = 0x2047, 0x0300
BAUD = 128000
ECG_FS, PPG_FS = 500.0, 50.0
VOLT_PER_CODE = 1.2 / 2 ** 21
MAX_SECONDS = 600          # keep the newest 10 minutes
DETECT_AFTER_S = 20        # seconds of data used to find the PPG slot
START_VARIANTS = [("00", "00000000"), ("00", "FFFFFFFF"), ("01", "FFFFFFFF")]


# ---------------------------------------------------------------- ports
def list_serial_ports():
    """All serial ports with their USB ids, for the hardware panel."""
    rows = []
    for q in list_ports.comports():
        rows.append({"port": q.device,
                     "vid:pid": "" if q.vid is None else f"{q.vid:04X}:{q.pid:04X}",
                     "description": q.description,
                     "afe_candidate": q.vid == VID and q.pid == PID})
    return rows


def probe_port(device, wait=0.4):
    """Ask a port what it is. Returns 'CAPTURE', 'REG_EDIT', other text, or 'error: ...'."""
    try:
        with serial.Serial(device, BAUD, timeout=0.5) as s:
            s.reset_input_buffer()
            s.write(b"C:CDC?\n")
            time.sleep(wait)
            reply = s.read(s.in_waiting or 32)
        return reply.decode(errors="replace").strip() or "no reply"
    except Exception as e:  # port busy, missing, permission
        return f"error: {e}"


def find_capture_port():
    """Return (port, report). port is None when no CAPTURE port answered."""
    report = []
    for row in list_serial_ports():
        if not row["afe_candidate"]:
            continue
        answer = probe_port(row["port"])
        report.append(f"{row['port']}: {answer}")
        if answer.upper().startswith("CAPTURE"):
            return row["port"], report
    if not report:
        report.append("No port with USB id 2047:0300 found. Is the AFE plugged in and is the TI GUI closed?")
    return None, report


# ---------------------------------------------------------------- parsing
def parse_line(line):
    """One data line to a list of signed 24-bit codes. None if it is not a data line."""
    line = line.strip("\r")
    words = line.split("\t")
    if words and words[-1] == "":
        words = words[:-1]
    if len(words) < 2 or words[0] == "EXTRA_REG":
        return None
    try:
        n = (int(words[0], 16) + 1) & 0xFF
        codes = []
        for i in range(n):
            u = int(words[i + 1], 16) & 0xFFFFFF
            codes.append(u - 2 ** 24 if u >= 2 ** 23 else u)
        return codes
    except (ValueError, IndexError):
        return None


def load_fifo_seq(path):
    """Read a *_FIFO_SEQ.cfg file: one channel name per line. Returns a list like ['ECG', ..., 'TIA1-3']."""
    names = [ln.strip() for ln in Path(path).read_text().splitlines() if ln.strip()]
    return names


def is_ppg_name(name):
    return name.upper().startswith("TIA") or "PPG" in name.upper()


# ---------------------------------------------------------------- stream
class AFEStream:
    def __init__(self):
        self.lock = threading.Lock()
        self.thread = None
        self.stop_flag = threading.Event()
        self.reset()

    def reset(self):
        self.state = "idle"        # idle, connecting, starting, calibrating, streaming, error, stopped
        self.message = "Not started"
        self.port = None
        self.variant = None
        self.layout = None         # list of 'E' or 'P', one entry per word in the cycle
        self.fifo_seq = None
        self.lines = 0
        self.bad_lines = 0
        self.words_total = 0
        self.rate = 0.0
        self.pending = []          # raw codes before the layout is known
        cap = int(MAX_SECONDS * ECG_FS)
        self.ecg = collections.deque(maxlen=cap)
        self.ppg = collections.deque(maxlen=int(MAX_SECONDS * PPG_FS))
        self.n_ecg = 0
        self.n_ppg = 0
        self.raw_tail = collections.deque(maxlen=40)
        self.t_started = None
        self.last_data = None

    # ----- public
    def start(self, port=None, fifo_seq=None, ppg_slot=None):
        if self.thread and self.thread.is_alive():
            return
        self.reset()
        self.fifo_seq = fifo_seq
        self.manual_slot = ppg_slot
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self._run, args=(port,), daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_flag.set()
        if self.thread:
            self.thread.join(timeout=3)
        if self.state not in ("error",):
            self.state, self.message = "stopped", "Stopped"

    def window(self, sec):
        """Newest sec seconds as (ecg_t, ecg_v, ppg_t, ppg_v). Time starts at 0 at capture start."""
        with self.lock:
            ev = np.array(self.ecg, float)
            pv = np.array(self.ppg, float)
            n_e, n_p = self.n_ecg, self.n_ppg
        et = (n_e - len(ev) + np.arange(len(ev))) / ECG_FS
        pt = (n_p - len(pv) + np.arange(len(pv))) / PPG_FS
        if len(ev):
            lo = et[-1] - sec
            m, mp = et >= lo, pt >= lo
            return et[m], ev[m], pt[mp], pv[mp]
        return et, ev, pt, pv

    def save_csv(self, path):
        """Save everything captured in the TI export format so analyze_signals.py can read it."""
        with self.lock:
            ev, pv = np.array(self.ecg), np.array(self.ppg)
            n_e, n_p = self.n_ecg, self.n_ppg
        et = (n_e - len(ev) + np.arange(len(ev))) / ECG_FS
        pt = (n_p - len(pv) + np.arange(len(pv))) / PPG_FS
        df = pd.concat([pd.Series(et, name="ECG_Time"), pd.Series(ev, name="ECG_Value"),
                        pd.Series(pt, name="TIA1-3_Time"), pd.Series(pv, name="TIA1-3_Value")], axis=1)
        df.to_csv(path, index=False)
        return len(ev), len(pv)

    # ----- internals
    def _set(self, state, message):
        self.state, self.message = state, message

    def _layout_from_seq(self, seq):
        return ["P" if is_ppg_name(n) else "E" for n in seq]

    def _push(self, codes):
        for c in codes:
            g = self.words_total
            self.words_total += 1
            if self.layout is None:
                self.pending.append(c)
                continue
            self._store(g, c)

    def _store(self, g, c):
        v = c * VOLT_PER_CODE
        kind = self.layout[g % len(self.layout)]
        with self.lock:
            if kind == "E":
                self.ecg.append(v)
                self.n_ecg += 1
            else:
                self.ppg.append(v)
                self.n_ppg += 1

    def _apply_layout(self, layout):
        self.layout = layout
        pend, self.pending = self.pending, []
        for g, c in enumerate(pend):
            self._store(g, c)

    def _detect_slot(self):
        """Try every PPG position in an 11-word cycle. Keep the one with the best signal quality."""
        cycle = int(round((ECG_FS + PPG_FS) / PPG_FS))
        words = np.array(self.pending, float) * VOLT_PER_CODE
        idx = np.arange(len(words))
        best = (-1.0, None)
        for k in range(cycle):
            is_p = (idx % cycle) == k
            ev, pv = words[~is_p], words[is_p]
            et = np.arange(len(ev)) / ECG_FS
            pt = np.arange(len(pv)) / PPG_FS
            try:
                res = p.analyze(et, ev, pt, pv)
            except Exception:
                continue
            score = res.sqi + (10 if res.hr_ecg and res.hr_ppg and abs(res.hr_ecg - res.hr_ppg) < 5 else 0)
            if score > best[0]:
                best = (score, k, res.sqi, res.hr_ecg)
        if best[1] is None or best[2] < p.SQI_SHOW:
            return None, best
        return ["P" if i == best[1] else "E" for i in range(cycle)], best

    def _run(self, port):
        ser = None
        try:
            self._set("connecting", "Looking for the AFE capture port")
            if port is None:
                port, report = find_capture_port()
                if port is None:
                    self._set("error", " | ".join(report))
                    return
            self.port = port
            ser = serial.Serial(port, BAUD, timeout=0.2)
            ser.reset_input_buffer()

            if self.fifo_seq:
                self.layout = self._layout_from_seq(self.fifo_seq)
            elif self.manual_slot is not None:
                cyc = int(round((ECG_FS + PPG_FS) / PPG_FS))
                self.layout = ["P" if i == self.manual_slot else "E" for i in range(cyc)]

            # Start streaming. Try the start command variants until bytes arrive.
            buf = b""
            got = False
            for cont, count in START_VARIANTS:
                if self.stop_flag.is_set():
                    return
                self._set("starting", f"Sending C:CAP_START{cont}{count}")
                ser.write(("C:CAP_START" + cont + count).upper().encode())
                t0 = time.time()
                while time.time() - t0 < 4 and not self.stop_flag.is_set():
                    chunk = ser.read(ser.in_waiting or 1)
                    if chunk:
                        buf += chunk
                        got = True
                        break
                if got:
                    self.variant = cont + count
                    break
                ser.write(b"C:CAP_STOP")
                time.sleep(0.3)
                ser.reset_input_buffer()
            if not got:
                self._set("error", "No data after C:CAP_START. Prime the AFE with the TI GUI first "
                                   "(load settings, start one capture, close the GUI), then try again.")
                return

            self.t_started = time.time()
            last_rate_t, last_rate_n = time.time(), 0
            detect_tried_at = 0
            self._set("streaming" if self.layout else "calibrating",
                      "Streaming" if self.layout else f"Collecting {DETECT_AFTER_S} s to find the PPG slot")
            while not self.stop_flag.is_set():
                chunk = ser.read(ser.in_waiting or 1)
                if chunk:
                    buf += chunk
                    self.last_data = time.time()
                    *lines, buf = buf.split(b"\n")
                    for raw in lines:
                        txt = raw.decode(errors="replace")
                        self.raw_tail.append(txt[:160])
                        self.lines += 1
                        codes = parse_line(txt)
                        if codes is None:
                            self.bad_lines += 1
                            continue
                        self._push(codes)
                now = time.time()
                if now - last_rate_t >= 1.0:
                    self.rate = (self.words_total - last_rate_n) / (now - last_rate_t)
                    last_rate_t, last_rate_n = now, self.words_total
                if self.layout is None and len(self.pending) >= DETECT_AFTER_S * (ECG_FS + PPG_FS) \
                        and now - detect_tried_at > 10:
                    detect_tried_at = now
                    layout, best = self._detect_slot()
                    if layout is not None:
                        self._apply_layout(layout)
                        self._set("streaming", f"Streaming. PPG slot {layout.index('P')} found "
                                               f"(quality {best[2]:.0f}, HR {best[3]:.0f} BPM)")
                    else:
                        self._set("calibrating", "Could not find a clean heartbeat yet. Check electrodes and "
                                                 "contact, or load the FIFO_SEQ.cfg file.")
                if self.state == "streaming" and self.last_data and now - self.last_data > 3:
                    self._set("error", "Data stopped arriving")
            ser.write(b"C:CAP_STOP")
        except Exception as e:
            self._set("error", f"{type(e).__name__}: {e}")
        finally:
            try:
                if ser is not None:
                    ser.close()
            except Exception:
                pass

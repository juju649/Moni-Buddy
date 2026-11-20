"""Local bridge: CardioTwin results -> Moni-Buddy HTML dashboard (Live Monitor).

One result per second comes from the SK-AM62B board over serial, or from a recording replay on this laptop
(board_processor.py run locally, same code as on the board). Each result goes through twin.py (baseline) and
pushevents.classify (risk), and is served as JSON.

    python live_bridge.py                       replay on laptop (no board needed)
    python live_bridge.py --board COM11         read the SK-AM62B over its debug USB serial port
    python live_bridge.py --watch-port COM7     optional: also send  W,<patient>,<bpm>,<state>  to the ESP32 watch

Endpoints (port 8001, CORS on):
    GET /live?patient=hamantha|sam      newest result as JSON
    GET /stream?patient=hamantha|sam    the same, as Server-Sent Events once per second
    GET /health

Values come from RECORDINGS (replayed on the board or on this laptop). They are not live sensor data.
Nothing here is sent to the Moni-Buddy backend or Supabase.
EXPERIMENTAL student prototype. Not a medical device. The risk number is a demo heuristic.
"""
import argparse
import asyncio
import json
import threading
import time
from pathlib import Path

import numpy as np
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

import pipeline as p
from board_link import BoardLink
from pushevents import classify
from twin import Twin

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

PATIENTS = {
    "hamantha": {"name": "Healthy Hamantha", "csv": DATA / "AFE4950_CAPTURED_DATA.csv"},
    "sam": {"name": "Sickly Sam", "csv": DATA / "PATIENT2_HIGH_HR.csv"},
}
HIGH_BPM, LOW_BPM = 100, 50   # dashboard alert thresholds requested for the demo
WAVE_S = 5.0                  # seconds of ECG/PPG sent with each result
ECG_STEP = 4                  # 500 Hz -> 125 Hz for the browser


class Channel:
    """One BoardLink plus its own twin. Polled once per second."""

    def __init__(self, key, link, source_label):
        self.key, self.link, self.source_label = key, link, source_label
        self.twin = Twin(persist=False)
        self.last_seq = None
        self.latest = None
        self.lock = threading.Lock()

    def step(self):
        with self.link.lock:
            f = dict(self.link.last) if self.link.last else None
        if f is None or f.get("seq") == self.last_seq:
            return
        self.last_seq = f.get("seq")
        r, (et, ev, pt, pv) = self.link.result(sec=WAVE_S)
        summary = self.twin.update(r)
        hs = f.get("hs")
        valid = hs is not None and float(f.get("sqi", 0)) >= p.SQI_SHOW
        state, risk = "no_reading", None
        if valid:
            state, risk = classify(hs, f.get("cv"))
            if hs > HIGH_BPM:
                state = "elevated_hr"
            elif hs < LOW_BPM:
                state = "low_hr"
            elif state in ("elevated_hr", "low_hr"):
                state = "normal"
            if state in ("elevated_hr", "low_hr"):
                risk = max(risk, 0.75)  # so the dashboard risk band shows High during an alert (demo heuristic)
        tw = self.twin
        payload = {
            "patient": self.key,
            "patient_name": PATIENTS.get(self.key, {}).get("name", self.key),
            "seq": f.get("seq"),
            "t": f.get("t"),
            "hr": f.get("hr"),
            "hs": hs,
            "rm": f.get("rm"),
            "cv": f.get("cv"),
            "sqi": f.get("sqi"),
            "lab": f.get("lab"),
            "rej": f.get("rej", 0),
            "valid": valid,
            "state": state,
            "risk": risk,
            "alert": state in ("elevated_hr", "low_hr"),
            "baseline": {"heartRate": tw.baseline_hr, "hrv": tw.baseline_rmssd,
                         "good_windows": len(tw._good_hr), "needed": 20},
            "twin_alerts": summary.get("alerts", []),
            "ecg": _clean(ev[::ECG_STEP]),
            "ecg_fs": 500.0 / ECG_STEP,
            "ppg": _clean(pv),
            "ppg_fs": 50.0,
            "file": f.get("file"),
            "source": self.source_label,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        with self.lock:
            self.latest = payload

    def get(self):
        with self.lock:
            if self.latest:
                return self.latest
        return {"patient": self.key, "patient_name": PATIENTS.get(self.key, {}).get("name", self.key),
                "valid": False, "state": "waiting", "alert": False, "source": self.source_label,
                "message": self.link.message, "link_state": self.link.state,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def _clean(a):
    a = np.asarray(a, dtype=float)
    return [None if not np.isfinite(v) else round(float(v), 5) for v in a]


def build_channels(board_port):
    if board_port:
        # The board plays whatever recordings it has. Both patient keys read the same board stream.
        link = BoardLink()
        link.start_serial(board_port)
        ch = Channel("board", link, "recording replayed on SK-AM62B")
        return {"hamantha": ch, "sam": ch}, [ch]
    chans = {}
    for key, info in PATIENTS.items():
        link = BoardLink()
        link.start_simulated(info["csv"])
        chans[key] = Channel(key, link, "recording replay on laptop")
    return chans, list(chans.values())


def watch_writer(port, chans, state):
    """Optional: one line per second to the ESP32 watch over USB serial: W,<patient>,<bpm>,<state>"""
    import serial
    ser = serial.Serial(port, 115200, timeout=0.5)
    while True:
        key = state["watch_patient"]
        d = chans[key].get()
        bpm = int(round(d["hs"])) if d.get("valid") and d.get("hs") is not None else 0
        ser.write(f"W,{key},{bpm},{d.get('state', 'waiting')}\n".encode())
        time.sleep(1.0)


def make_app(chans, unique, state):
    app = FastAPI(title="CardioTwin live bridge")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

    def loop():
        while True:
            for ch in unique:
                try:
                    ch.step()
                except Exception as e:  # keep serving
                    print("bridge step error:", e)
            time.sleep(0.25)

    threading.Thread(target=loop, daemon=True).start()

    def pick(patient):
        key = patient if patient in chans else "hamantha"
        state["watch_patient"] = key
        return chans[key].get()

    @app.get("/health")
    def health():
        return {"ok": True, "links": {c.key: c.link.state for c in unique}}

    @app.get("/live")
    def live(patient: str = Query("hamantha")):
        return pick(patient)

    @app.get("/stream")
    async def stream(patient: str = Query("hamantha")):
        async def gen():
            last = None
            while True:
                d = pick(patient)
                if d.get("seq") != last:
                    last = d.get("seq")
                    yield "data: " + json.dumps(d) + "\n\n"
                await asyncio.sleep(0.5)
        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", default=None, help="SK-AM62B serial port, for example COM11. Omit for laptop replay")
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--watch-port", default=None, help="optional ESP32 watch serial port (off by default)")
    a = ap.parse_args()

    chans, unique = build_channels(a.board)
    state = {"watch_patient": "hamantha"}
    if a.watch_port:
        threading.Thread(target=watch_writer, args=(a.watch_port, chans, state), daemon=True).start()
    import uvicorn
    uvicorn.run(make_app(chans, unique, state), host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()

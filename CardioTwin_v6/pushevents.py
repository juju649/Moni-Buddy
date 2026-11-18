"""Turn a TI AFE4950 capture into Moni-Buddy dashboard events.

Usage:
  python pushevents.py                       write dashboard/events.json
  python pushevents.py --demo-alert          also add one SIMULATED alert (marked)
  python pushevents.py --post http://localhost:8000
                                             also POST real events to the FastAPI backend

Each event matches the backend Event model: event_id, patient_id, state, risk, hr, timestamp.
EXPERIMENTAL. The risk number is a simple demo heuristic, not a clinical score.
"""
import argparse
import json
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

import pipeline as p

HERE = Path(__file__).resolve().parent
NS = uuid.UUID("6f1c2b9e-3a45-4d7e-9c10-5b8d2e7a4f33")  # fixed, so reruns upsert the same rows


def classify(hr, cv):
    """Same thresholds as the CardioTwin twin. Returns (state, risk 0..1)."""
    if hr > 120:
        state = "elevated_hr"
    elif hr < 45:
        state = "low_hr"
    elif cv is not None and cv > 0.15:
        state = "irregular_rhythm"
    else:
        state = "normal"
    hr_dev = max(0.0, (hr - 100) / 60.0, (50 - hr) / 30.0)
    irr = 0.0 if cv is None else (cv - 0.08) / 0.2
    risk = 0.10 + 0.60 * float(np.clip(max(hr_dev, irr), 0, 1))
    return state, round(risk, 2)


def build_events(csv_path, patient, window_s, step_s, start, stretch=1.0):
    et, ev, pt, pv = p.load_capture(csv_path)
    end = float(et[-1])
    events = []
    t = window_s
    while t <= end + 1e-9:
        me = (et > t - window_s) & (et <= t)
        mp = (pt > t - window_s) & (pt <= t)
        r = p.analyze(et[me], ev[me], pt[mp], pv[mp])
        if r.hr_ecg is not None and r.sqi >= p.SQI_SHOW:   # skip unreliable windows
            state, risk = classify(r.hr_ecg, r.rr_cv)
            events.append({
                "event_id": str(uuid.uuid5(NS, f"{patient}|{Path(csv_path).name}|{t:.1f}")),
                "patient_id": patient,
                "state": state,
                "risk": risk,
                "hr": int(round(r.hr_ecg)),
                "timestamp": (start + timedelta(seconds=t * stretch)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "acknowledged_by": None,
                "acknowledged_at": None,
            })
        t += step_s
    return events


def post(base, ev):
    body = {k: ev[k] for k in ("event_id", "patient_id", "state", "risk", "hr", "timestamp")}
    req = urllib.request.Request(base.rstrip("/") + "/events", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(HERE / "data" / "AFE4950_CAPTURED_DATA.csv"))
    ap.add_argument("--patient", default="p1")
    ap.add_argument("--window", type=float, default=20.0, help="seconds analyzed per event")
    ap.add_argument("--step", type=float, default=5.0, help="seconds between events")
    ap.add_argument("--out", default=str(HERE / "dashboard" / "events.json"))
    ap.add_argument("--stretch", type=float, default=1.0,
                    help="replay clock factor. 30 spreads a 1 minute recording over 30 minutes on the dashboard")
    ap.add_argument("--post", default=None, help="backend base URL, for example http://localhost:8000")
    ap.add_argument("--demo-alert", action="store_true", help="append one SIMULATED high-risk event")
    a = ap.parse_args()

    et, *_ = p.load_capture(a.csv)
    start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=float(et[-1]) * a.stretch)
    events = build_events(a.csv, a.patient, a.window, a.step, start, a.stretch)
    if not events:
        raise SystemExit("No reliable windows found. Check the recording.")
    print(f"{len(events)} real events from {a.csv}")
    print("states:", {s: sum(e['state'] == s for e in events) for s in sorted({e['state'] for e in events})})

    if a.post:
        ok = sum(1 for e in events if post(a.post, e) in (200, 201))
        print(f"posted {ok} of {len(events)} real events to {a.post}")

    if a.demo_alert:   # never posted to the backend, and marked in the file
        last = events[-1]
        t = datetime.strptime(last["timestamp"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(seconds=5 * a.stretch)
        events.append({"event_id": str(uuid.uuid5(NS, "simulated-alert")), "patient_id": a.patient,
                       "state": "elevated_hr", "risk": 0.82, "hr": 128,
                       "timestamp": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "acknowledged_by": None, "acknowledged_at": None, "source": "simulated"})
        print("added 1 simulated alert (marked, not posted)")

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(events, indent=1))
    print("wrote", a.out)


if __name__ == "__main__":
    main()

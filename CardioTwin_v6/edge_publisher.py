"""Send AFE4950 results to the Moni-Buddy backend, event by event.

Runs on the laptop today. Runs on the SK-AM62B once numpy, scipy and pyserial are installed there.

Live from the AFE over USB:
  python edge_publisher.py --url http://BACKEND:8000
Replay of your recorded capture (no hardware):
  python edge_publisher.py --url http://BACKEND:8000 --replay data/AFE4950_CAPTURED_DATA.csv --speed 5
Preview only, nothing is sent:
  python edge_publisher.py --dry-run --replay data/AFE4950_CAPTURED_DATA.csv --speed 20

Every 5 seconds it analyzes the last 20 seconds, builds one event and POSTs it to URL + /events.
Events that fail to send wait in data/publish_queue.jsonl and go out when the network returns.
Windows with signal quality below 55 are skipped, so a loose electrode sends nothing.
EXPERIMENTAL student prototype. Not a medical device. The risk number is a demo heuristic.
"""
import argparse
import json
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pipeline as p
from pushevents import classify
from source import LiveSource, ReplaySource

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
FIELDS = ("event_id", "patient_id", "state", "risk", "hr", "timestamp")


def post_event(base, path, ev, headers, timeout=5):
    body = json.dumps({k: ev[k] for k in FIELDS}).encode()
    h = {"Content-Type": "application/json"}
    h.update(headers)
    req = urllib.request.Request(base.rstrip("/") + path, data=body, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


class Queue:
    """Events that could not be sent. Kept on disk so a restart loses nothing."""

    def __init__(self, path):
        self.path = Path(path)
        self.items = []
        if self.path.exists():
            self.items = [json.loads(x) for x in self.path.read_text().splitlines() if x.strip()]

    def save(self):
        self.path.write_text("".join(json.dumps(e) + "\n" for e in self.items))

    def add(self, ev):
        self.items.append(ev)
        self.save()

    def flush(self, send, limit=25):
        sent = 0
        while self.items and sent < limit:
            try:
                send(self.items[0])
            except Exception:
                break
            self.items.pop(0)
            sent += 1
        if sent:
            self.save()
        return sent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000", help="backend base URL")
    ap.add_argument("--path", default="/events", help="endpoint path")
    ap.add_argument("--patient", default="p1")
    ap.add_argument("--window", type=float, default=20.0, help="seconds analyzed per event")
    ap.add_argument("--step", type=float, default=5.0, help="seconds between events")
    ap.add_argument("--header", action="append", default=[], help='extra header, for example "Authorization: Bearer KEY"')
    ap.add_argument("--replay", default=None, help="CSV to replay instead of the live AFE")
    ap.add_argument("--speed", type=float, default=1.0, help="replay speed")
    ap.add_argument("--port", default=None, help="serial port for live mode, for example COM5")
    ap.add_argument("--dry-run", action="store_true", help="print events, send nothing")
    a = ap.parse_args()

    headers = {}
    for h in a.header:
        k, _, v = h.partition(":")
        headers[k.strip()] = v.strip()

    DATA.mkdir(exist_ok=True)
    queue = Queue(DATA / "publish_queue.jsonl")
    log = DATA / "published_events.jsonl"
    stream = None
    if a.replay:
        src = ReplaySource(Path(a.replay))
        print(f"Replaying {a.replay}. Source is a RECORDING, not live hardware.")
    else:
        import afe_serial as afe
        stream = afe.AFEStream()
        stream.start(port=a.port)
        src = LiveSource(stream)
        print("Live mode. Source is the AFE4950 over USB.")

    def send(ev):
        return post_event(a.url, a.path, ev, headers)

    sent_total = skipped = 0
    try:
        while True:
            if a.replay:
                src.advance(a.step)
                if src.at_end:
                    break
            elif stream.state not in ("streaming", "calibrating"):
                print(f"AFE {stream.state}: {stream.message}")
                time.sleep(2)
                continue
            w = src.window(a.window)
            span = float(w[0][-1] - w[0][0]) if len(w[0]) else 0.0
            if span < a.window * 0.9:
                print(f"Collecting signal ({span:.0f} of {a.window:.0f} s)")
            else:
                r = p.analyze(*w)
                if r.hr_ecg is None or r.sqi < p.SQI_SHOW:
                    skipped += 1
                    print(f"Skipped window. Signal quality {r.sqi:.0f}. Nothing sent.")
                else:
                    state, risk = classify(r.hr_ecg, r.rr_cv)
                    ev = {"event_id": str(uuid.uuid4()), "patient_id": a.patient, "state": state,
                          "risk": risk, "hr": int(round(r.hr_ecg)),
                          "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
                    with log.open("a") as f:
                        f.write(json.dumps(ev) + "\n")
                    if a.dry_run:
                        print("DRY RUN", json.dumps(ev))
                    else:
                        queue.add(ev)
                        n = queue.flush(send)
                        sent_total += n
                        print(f"hr {ev['hr']} state {state} risk {risk}  sent {n}  waiting {len(queue.items)}")
            if not a.dry_run and queue.items and span < a.window * 0.9:
                sent_total += queue.flush(send)
            time.sleep(a.step / a.speed if a.replay else a.step)
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        if stream is not None:
            stream.stop()
    print(f"Done. {sent_total} events sent, {skipped} windows skipped, {len(queue.items)} still waiting.")


if __name__ == "__main__":
    sys.exit(main())

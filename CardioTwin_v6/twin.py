"""Digital twin state kept on the edge device.

The twin stores a running baseline and a short history of summaries. Raw
samples never leave this module. Each update appends one summary to a local
log and to an outbound sync queue, so nothing is lost if the network drops.
Alerts are EXPERIMENTAL indicators. They are not diagnoses.
"""
import json
import time
from collections import deque
from pathlib import Path

from pipeline import SQI_GOOD, SQI_SHOW

DATA = Path(__file__).resolve().parent / "data"


class Twin:
    def __init__(self, data_dir=DATA, persist=True):
        self.dir = Path(data_dir)
        self.persist = persist
        self.history = deque(maxlen=600)  # about 10 min at 1 summary per second
        self.baseline_hr = None
        self.baseline_rmssd = None
        self._good_hr = deque(maxlen=120)
        self._good_rmssd = deque(maxlen=120)
        self.online = True  # set False to test the offline queue
        if persist:
            self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _median(xs):
        s = sorted(xs)
        return s[len(s) // 2] if s else None

    def update(self, r):
        """Fold one analysis Result into the twin and return a summary dict."""
        hr = r.hr_display
        if hr is not None and r.sqi >= SQI_GOOD:
            self._good_hr.append(hr)
            if r.rmssd_ms is not None:
                self._good_rmssd.append(r.rmssd_ms)
            if len(self._good_hr) >= 20:
                self.baseline_hr = self._median(self._good_hr)
                self.baseline_rmssd = self._median(self._good_rmssd)

        alerts = self._alerts(r, hr)
        summary = {
            "t": round(r.t_end, 2),
            "wall_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "hr": None if hr is None else round(hr, 1),
            "hrv_rmssd_ms": None if r.rmssd_ms is None else round(r.rmssd_ms, 1),
            "resp_bpm_experimental": None if r.resp_bpm is None else round(r.resp_bpm, 1),
            "sqi": r.sqi,
            "quality": r.sqi_label,
            "alerts": alerts,
        }
        self.history.append(summary)
        if self.persist:
            self._write(summary)
        return summary

    def _alerts(self, r, hr):
        out = []
        if not r.contact:
            return ["no contact or no signal"]
        if r.sqi < SQI_SHOW:
            return ["signal unreliable, readings suppressed"]
        if hr is not None:
            if hr > 120:
                out.append("high heart-rate indicator (>120 BPM)")
            elif hr < 45:
                out.append("low heart-rate indicator (<45 BPM)")
            if self.baseline_hr and abs(hr - self.baseline_hr) > 0.25 * self.baseline_hr:
                out.append(f"heart rate 25% away from your baseline ({self.baseline_hr:.0f} BPM)")
        if r.rr_cv is not None and r.rr_cv > 0.15:
            out.append("irregular beat-timing indicator (experimental)")
        if r.resp_bpm is not None and r.resp_agree and (r.resp_bpm > 25 or r.resp_bpm < 8):
            out.append("breathing-rate indicator outside 8 to 25 per minute (experimental)")
        return out

    def _write(self, s):
        with open(self.dir / "twin_log.jsonl", "a") as f:
            f.write(json.dumps(s) + "\n")
        # Outbound queue: summaries only. A sync worker drains it when online.
        with open(self.dir / "sync_queue.jsonl", "a") as f:
            f.write(json.dumps(s) + "\n")
        (self.dir / "twin_state.json").write_text(json.dumps({
            "baseline_hr": self.baseline_hr,
            "baseline_rmssd_ms": self.baseline_rmssd,
            "last": s,
        }, indent=2))

    def queue_depth(self):
        p = self.dir / "sync_queue.jsonl"
        return sum(1 for _ in open(p)) if p.exists() else 0

    def drain_queue(self, send):
        """Call send(list_of_summaries). Clears the queue only on success."""
        p = self.dir / "sync_queue.jsonl"
        if not (self.online and p.exists()):
            return 0
        rows = [json.loads(x) for x in open(p)]
        if rows and send(rows):
            p.write_text("")
            return len(rows)
        return 0

"""Analyze a recorded AFE4950 capture and write the results the dashboard reads.

Run:  python analyze_signals.py
Or:   python analyze_signals.py path/to/other_capture.csv

Reads   data/AFE4950_CAPTURED_DATA.csv   (the original TI export, never changed)
Writes  data/metrics.json                recording-level results
        data/candidate_matches.csv       ECG R-peak and PPG peak candidate pairs

EXPERIMENTAL. Not a medical device. It does not diagnose anything.
ECG/PPG pairs are internal matching events. They are not validated pulse
transit time and they are not a blood-pressure reading.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import pipeline as p

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DEFAULT_CSV = DATA / "AFE4950_CAPTURED_DATA.csv"

# A pulse peak must arrive this long after the R-peak to count as a candidate pair.
MATCH_MIN_S = 0.10
MATCH_MAX_S = 0.60


def pair_candidates(ecg_t, r_idx, ppg_t, ppg_idx):
    """Pair each R-peak with the first unused PPG peak 0.10 to 0.60 s later."""
    r_times = ecg_t[r_idx]
    p_times = ppg_t[ppg_idx]
    rows, used = [], set()
    for rt in r_times:
        for j, pt in enumerate(p_times):
            if j in used:
                continue
            d = pt - rt
            if d < MATCH_MIN_S:
                continue
            if d > MATCH_MAX_S:
                break
            rows.append({"ecg_peak_time_s": round(float(rt), 4),
                         "ppg_peak_time_s": round(float(pt), 4),
                         "delay_ms": round(float(d) * 1000, 1)})
            used.add(j)
            break
    return pd.DataFrame(rows, columns=["ecg_peak_time_s", "ppg_peak_time_s", "delay_ms"])


def analyze_file(csv_path):
    ecg_t, ecg_v, ppg_t, ppg_v = p.load_capture(csv_path)
    res = p.analyze(ecg_t, ecg_v, ppg_t, ppg_v)
    n_ecg = 0 if res.ecg_peaks is None else len(res.ecg_peaks)
    n_ppg = 0 if res.ppg_peaks is None else len(res.ppg_peaks)
    matches = (pair_candidates(ecg_t, res.ecg_peaks, ppg_t, res.ppg_peaks)
               if n_ecg and n_ppg else pd.DataFrame(columns=["ecg_peak_time_s", "ppg_peak_time_s", "delay_ms"]))

    def r(x, d=1):
        return None if x is None else round(float(x), d)

    metrics = {
        "source_file": Path(csv_path).name,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_s": r(ecg_t[-1] - ecg_t[0], 2),
        "ecg_sample_rate_hz": r(p.sample_rate(ecg_t), 0),
        "ppg_sample_rate_hz": r(p.sample_rate(ppg_t), 0),
        "ecg_samples": int(len(ecg_t)),
        "ppg_samples": int(len(ppg_t)),
        "ecg_beats": n_ecg,
        "ppg_peaks": n_ppg,
        "ecg_ppg_matches": int(len(matches)),
        "heart_rate_ecg_bpm": r(res.hr_ecg, 2),
        "heart_rate_ppg_bpm": r(res.hr_ppg, 2),
        "rmssd_ms": r(res.rmssd_ms, 1),
        "snr_db": r(res.snr_db, 1),
        "signal_quality": r(res.sqi, 1),
        "signal_quality_label": res.sqi_label,
        "breathing_per_min_experimental": r(res.resp_bpm, 1),
        "ecg_polarity_flipped": bool(res.polarity_flipped),
        "quality_notes": list(res.reasons),
        "disclaimer": "Experimental student prototype. Not a medical device. Does not diagnose.",
    }
    return metrics, matches


def main():
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV
    if not csv_path.exists():
        sys.exit(f"CSV not found: {csv_path}\nPut the TI export in the data folder as {DEFAULT_CSV.name}")
    DATA.mkdir(exist_ok=True)
    metrics, matches = analyze_file(csv_path)
    (DATA / "metrics.json").write_text(json.dumps(metrics, indent=2))
    matches.to_csv(DATA / "candidate_matches.csv", index=False)

    hr = metrics["heart_rate_ecg_bpm"]
    print("TRACK 1 - REAL AFE4950 ANALYSIS")
    print(f"ECG beats: {metrics['ecg_beats']}")
    print(f"Candidate PPG peaks: {metrics['ppg_peaks']}")
    print(f"ECG-PPG candidate matches: {metrics['ecg_ppg_matches']}")
    print(f"Heart rate: {'--' if hr is None else f'{hr:.2f}'} BPM")
    print(f"Saved to: {DATA}")
    print()
    print(f"Recording: {metrics['duration_s']} s, ECG {metrics['ecg_sample_rate_hz']:.0f} Hz, "
          f"PPG {metrics['ppg_sample_rate_hz']:.0f} Hz")
    print(f"PPG heart rate: {metrics['heart_rate_ppg_bpm']} BPM (independent check)")
    print(f"Signal quality: {metrics['signal_quality']} / 100 ({metrics['signal_quality_label']})")
    if metrics["quality_notes"]:
        print("Quality notes: " + "; ".join(metrics["quality_notes"]))


if __name__ == "__main__":
    main()

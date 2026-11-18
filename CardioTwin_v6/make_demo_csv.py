"""Make a synthetic CSV in the TI AFE4950 export format, for testing only.

Use it until you have your own capture. It mimics the reference file:
500 Hz ECG, 50 Hz PPG, 27.48 s, trailing delimiter on each row.
Synthetic data proves the code runs. It proves nothing about accuracy.
"""
import sys
from pathlib import Path

import numpy as np

out = Path(sys.argv[1] if len(sys.argv) > 1 else "data/demo_capture.csv")
out.parent.mkdir(parents=True, exist_ok=True)
rng = np.random.default_rng(7)
dur, fs_e, fs_p = 27.48, 500, 50
hr, resp = 85.0, 15.0  # BPM and breaths per minute used to build the signal

te = np.arange(0, dur, 1 / fs_e)
tp = np.arange(0, dur, 1 / fs_p)

# Beat times with respiratory sinus arrhythmia (heart rate varies with breathing).
beats, t = [], 0.3
while t < dur:
    beats.append(t)
    t += 60 / hr * (1 + 0.04 * np.sin(2 * np.pi * resp / 60 * t))
beats = np.array(beats)


def gauss(x, mu, s, a):
    return a * np.exp(-0.5 * ((x - mu) / s) ** 2)


ecg = np.zeros_like(te)
for b in beats:
    amp = 1 + 0.08 * np.sin(2 * np.pi * resp / 60 * b)  # breathing modulates R height
    ecg += gauss(te, b - 0.04, 0.010, -0.1) + gauss(te, b, 0.008, 1.0 * amp)
    ecg += gauss(te, b + 0.03, 0.010, -0.2) + gauss(te, b + 0.25, 0.04, 0.25)
    ecg += gauss(te, b - 0.16, 0.025, 0.12)
ecg = 30 * ecg + 1.2 * rng.standard_normal(len(te)) + 2 * np.sin(2 * np.pi * 0.2 * te)

ppg = np.zeros_like(tp)
for b in beats:
    ppg += gauss(tp, b + 0.18, 0.07, 1.0) + gauss(tp, b + 0.38, 0.09, 0.35)
ppg = ppg + 0.25 * np.sin(2 * np.pi * resp / 60 * tp) + 0.05 * rng.standard_normal(len(tp))

with open(out, "w") as f:
    f.write("ECG_Time,ECG_Value,TIA1-3_Time,TIA1-3_Value,\n")
    for i in range(len(te)):
        if i < len(tp):
            f.write(f"{te[i]:.4f},{ecg[i]:.5f},{tp[i]:.2f},{ppg[i]:.5f},\n")
        else:
            f.write(f"{te[i]:.4f},{ecg[i]:.5f},,,\n")
print(f"wrote {out}: {len(te)} ECG rows, {len(tp)} PPG rows")

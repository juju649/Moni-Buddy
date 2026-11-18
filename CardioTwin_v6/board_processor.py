"""board_processor.py  CardioTwin edge processor for the SK-AM62B.

Runs ON the board with plain Python 3. It needs NO numpy, NO scipy and NO internet.
It reads a recorded AFE4950 capture, filters the ECG and PPG, finds heartbeats, scores
signal quality, and prints one finished result line per second. The dashboard on your laptop
reads those lines and draws them. The laptop does no signal processing in this mode.

Copy two files to the board: this file and AFE4950_CAPTURED_DATA.csv. Then run:

  python3 board_processor.py --loop > $(tty) 2>&1 &

The output goes to the serial console (the debug micro-USB cable). Close PuTTY afterwards so the
dashboard can open the same COM port. Test without the dashboard:

  python3 board_processor.py --once --fast

Each result line looks like:  CT1 <crc32 in hex> <json>
Lines that do not start with CT1 are ignored by the dashboard.

EXPERIMENTAL student prototype. Not a medical device. It does not diagnose anything.
The filters and rules match pipeline.py on the laptop. Breathing rate is not computed here.
"""
import argparse
import bisect
import csv
import json
import math
import os
import sys
import time
import zlib

ECG_FS = 500.0
PPG_FS = 50.0
SQI_GOOD = 75
SQI_SHOW = 75  # show a heart rate only at this quality or higher
REJECT_FRAC = 0.20  # beat intervals more than 20% from the median are ignored for HR and HRV
TRIM_START_S = 2.0  # skip the first 2 s of each recording (filter settling)
TRIM_END_S = 1.0  # and the last 1 s
SMOOTH_N = 5  # shown heart rate is the median of the last 5 results
LAG_S = 1.5  # waveform is sent this far behind the newest sample, so filter edge ringing never shows

# Band-pass filter designs from scipy.signal.butter, order 3, output "sos".
# Stored as numbers so the board needs no scipy. zi is the steady-state start condition.
FILTERS = {'ecg': {'padlen': 21,
         'sos': [[0.009853425385307955, 0.01970685077061591, 0.009853425385307955, 1.0, -1.4211172619535009,
                  0.6191826971043422],
                 [1.0, 0.0, -1.0, 1.0, -1.5930745222504448, 0.5956466293893226],
                 [1.0, -2.0, 1.0, 1.0, -1.993775865592425, 0.9938156912938432]],
         'zi': [[0.18913990987894652, -0.11335980464940175], [-0.1989933352642545, -0.1989933352642545],
                [-0.0, 0.0]]},
 'ppg': {'padlen': 21,
         'sos': [[0.007167667426644551, 0.014335334853289102, 0.007167667426644551, 1.0, -1.4918577186916049,
                  0.6842315707238718],
                 [1.0, 0.0, -1.0, 1.0, -1.6084513056205172, 0.6346192975441481],
                 [1.0, -2.0, 1.0, 1.0, -1.9442586551338032, 0.9484429835806704]],
         'zi': [[0.14186854203579982, -0.0948076122685757], [-0.14903620946244436, -0.14903620946244436],
                [-0.0, 0.0]]},
 'qrs': {'padlen': 21,
         'sos': [[0.00021960621122536208, 0.00043921242245072416, 0.00021960621122536208, 1.0,
                  -1.8704724177979166, 0.8816185923631893],
                 [1.0, 0.0, -1.0, 1.0, -1.8820599780363987, 0.9125737665966678],
                 [1.0, -2.0, 1.0, 1.0, -1.9622489414822493, 0.9665605533185191]],
         'zi': [[0.07858992972034826, -0.06926034592156474], [-0.07880953593157361, -0.07880953593157361],
                [-0.0, 0.0]]}}


# ---------------------------------------------------------------- small math helpers
def median(xs):
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return None
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def percentile(xs, q):
    s = sorted(xs)
    pos = (q / 100.0) * (len(s) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def mean(xs):
    return sum(xs) / len(xs)


def std(xs):
    m = mean(xs)
    return math.sqrt(sum((v - m) ** 2 for v in xs) / len(xs))


# ---------------------------------------------------------------- zero-phase filter (scipy sosfiltfilt)
def _sosfilt(sos, zi, x, x0):
    y = x
    for s, (b0, b1, b2, _a0, a1, a2) in enumerate(sos):
        z0 = zi[s][0] * x0
        z1 = zi[s][1] * x0
        out = [0.0] * len(y)
        for i, v in enumerate(y):
            o = b0 * v + z0
            z0 = b1 * v - a1 * o + z1
            z1 = b2 * v - a2 * o
            out[i] = o
        y = out
    return y


def filtfilt(name, x):
    f = FILTERS[name]
    sos, zi, n = f["sos"], f["zi"], f["padlen"]
    left = [2.0 * x[0] - x[i] for i in range(n, 0, -1)]
    right = [2.0 * x[-1] - x[-1 - i] for i in range(1, n + 1)]
    ext = left + list(x) + right
    y = _sosfilt(sos, zi, ext, ext[0])
    y = _sosfilt(sos, zi, y[::-1], y[-1])
    y = y[::-1]
    return y[n:len(y) - n]


# ---------------------------------------------------------------- peak finding (scipy find_peaks rules)
def local_maxima(x):
    return [i for i in range(1, len(x) - 1) if x[i - 1] < x[i] > x[i + 1]]


def by_distance(peaks, x, distance):
    keep = [True] * len(peaks)
    order = sorted(range(len(peaks)), key=lambda j: x[peaks[j]])  # ascending, walk from the highest
    for j in reversed(order):
        if not keep[j]:
            continue
        k = j - 1
        while k >= 0 and peaks[j] - peaks[k] < distance:
            keep[k] = False
            k -= 1
        k = j + 1
        while k < len(peaks) and peaks[k] - peaks[j] < distance:
            keep[k] = False
            k += 1
    return [p for p, kp in zip(peaks, keep) if kp]


def prominence(x, peak):
    h = x[peak]
    lo_min = h
    i = peak
    while i >= 0 and x[i] <= h:
        lo_min = min(lo_min, x[i])
        i -= 1
    hi_min = h
    i = peak
    while i < len(x) and x[i] <= h:
        hi_min = min(hi_min, x[i])
        i += 1
    return h - max(lo_min, hi_min)


def find_peaks(x, height=None, distance=None, prom=None):
    pk = local_maxima(x)
    if height is not None:
        pk = [p for p in pk if x[p] >= height]
    if distance is not None and len(pk) > 1:
        pk = by_distance(pk, x, distance)
    if prom is not None:
        pk = [p for p in pk if prominence(x, p) >= prom]
    return pk


# ---------------------------------------------------------------- analysis (mirrors pipeline.py)
def detect_r_peaks(ecg):
    filt = filtfilt("ecg", ecg)
    qrs = [abs(v) for v in filtfilt("qrs", ecg)]
    thr = 0.35 * percentile(qrs, 99)
    cand = find_peaks(qrs, height=thr, distance=int(0.3 * ECG_FS))
    half = int(0.06 * ECG_FS)
    peaks = set()
    for c in cand:
        a, b = max(0, c - half), min(len(filt), c + half + 1)
        seg = [abs(v) for v in filt[a:b]]
        peaks.add(a + seg.index(max(seg)))
    margin = int(0.25 * ECG_FS)
    peaks = sorted(p for p in peaks if margin <= p < len(filt) - margin)
    return filt, peaks


def rr_stats(peaks):
    """Same rules as pipeline.rr_stats. rr feeds the quality score and cv. hr and rmssd use the cleaned intervals."""
    rr = [(peaks[i + 1] - peaks[i]) / ECG_FS for i in range(len(peaks) - 1)]
    rr = [v for v in rr if 0.3 < v < 2.0]
    if len(rr) < 3:
        return rr, None, None, None, 0
    med = median(rr)
    clean = [v for v in rr if abs(v - med) <= REJECT_FRAC * med]
    if len(clean) < 3:
        clean = rr
    hr = 60.0 / median(clean)
    rmssd = None
    if len(clean) > 3:
        d = [(clean[i + 1] - clean[i]) * 1000.0 for i in range(len(clean) - 1)]
        rmssd = math.sqrt(sum(v * v for v in d) / len(d))
    cv = std(rr) / mean(rr)
    return rr, hr, rmssd, cv, len(rr) - len(clean)


def ecg_snr_db(filt, peaks):
    if len(peaks) < 3:
        return None
    w = int(0.08 * ECG_FS)
    mask = [True] * len(filt)
    for p in peaks:
        for i in range(max(0, p - w), min(len(filt), p + w)):
            mask[i] = False
    noise_pts = [v for v, m in zip(filt, mask) if m]
    if len(noise_pts) <= 10:
        return None
    noise = std(noise_pts)
    sig = median([abs(filt[p]) for p in peaks])
    if not noise or noise <= 0:
        return None
    return 20.0 * math.log10(sig / noise)


def analyze(ecg, ppg):
    r = {"contact": False, "rej": 0, "hr": None, "hp": None, "rm": None, "cv": None, "sqi": 0.0,
         "lab": "NO SIGNAL", "flip": False, "why": [], "ecg": None, "rp": [], "ppg": None, "pp": []}
    if len(ecg) < 200:
        r["why"].append("window too short")
        return r
    if max(ecg) - min(ecg) < 1e-9 or std(ecg) < 1e-9:
        r["why"].append("flat ECG: leads off or no signal")
        return r
    r["contact"] = True
    filt, peaks = detect_r_peaks(ecg)
    if peaks and median([filt[p] for p in peaks]) < 0:
        filt = [-v for v in filt]
        r["flip"] = True
    r["ecg"], r["rp"] = filt, peaks
    rr, hr, rmssd, cv, rej = rr_stats(peaks)
    r["hr"], r["rm"], r["cv"], r["rej"] = hr, rmssd, cv, rej

    if len(ppg) > 100:
        pf = filtfilt("ppg", ppg)
        prom = 0.3 * (percentile(pf, 95) - percentile(pf, 5))
        pp = find_peaks(pf, distance=int(0.33 * PPG_FS), prom=max(prom, 1e-9))
        r["ppg"], r["pp"] = pf, pp
        if len(pp) >= 4:
            ibi = [(pp[i + 1] - pp[i]) / PPG_FS for i in range(len(pp) - 1)]
            ibi = [v for v in ibi if 0.3 < v < 2.0]
            if len(ibi) >= 3:
                r["hp"] = 60.0 / median(ibi)

    snr = ecg_snr_db(filt, peaks)
    score = 0.0
    if hr is None:
        r["why"].append("too few valid beats")
    else:
        score += 25
        med = median(rr)
        within = sum(1 for v in rr if abs(v - med) < 0.3 * med) / len(rr)
        score += 25 * within
        if within < 0.8:
            r["why"].append("irregular beat timing or missed beats")
    if snr is not None:
        score += 25 * min(max((snr - 6) / 14, 0.0), 1.0)
        if snr < 10:
            r["why"].append("noisy ECG")
    if hr and r["hp"]:
        if abs(hr - r["hp"]) <= 0.1 * hr:
            score += 25
        else:
            r["why"].append("ECG and PPG rates disagree")
    elif hr:
        score += 10
    r["sqi"] = round(score, 1)
    r["lab"] = "GOOD" if score >= SQI_GOOD else "FAIR" if score >= SQI_SHOW else "POOR"
    return r


# ---------------------------------------------------------------- recording and output
def load_csv(path):
    et, ev, pt, pv = [], [], [], []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            try:
                a, b = row.get("ECG_Time"), row.get("ECG_Value")
                if a not in (None, "") and b not in (None, ""):
                    et.append(float(a))
                    ev.append(float(b))
                c, d = row.get("TIA1-3_Time"), row.get("TIA1-3_Value")
                if c not in (None, "") and d not in (None, ""):
                    pt.append(float(c))
                    pv.append(float(d))
            except ValueError:
                continue
    return et, ev, pt, pv


def emit(frame):
    body = json.dumps(frame, separators=(",", ":"))
    crc = zlib.crc32(body.encode()) & 0xFFFFFFFF
    sys.stdout.write("CT1 %08x %s\n" % (crc, body))
    sys.stdout.flush()


def note(text):
    sys.stdout.write("# " + text + "\n")
    sys.stdout.flush()


def clip(et, ev, pt, pv, start, end):
    """Drop `start` s from the front and `end` s from the back of each series. The clock restarts at 0."""
    out = []
    for t, v in ((et, ev), (pt, pv)):
        lo = bisect.bisect_left(t, start - 1e-7)
        hi = bisect.bisect_right(t, t[-1] - end + 1e-7)
        out += [[x - start for x in t[lo:hi]], v[lo:hi]]
    return out


def is_recording(path):
    """True when the file is a TI GUI export with an ECG_Time column."""
    try:
        with open(path) as f:
            return "ECG_Time" in f.readline()
    except OSError:
        return False


def find_csvs():
    here = os.path.dirname(os.path.abspath(__file__))
    for folder in (here, os.path.join(here, "data"), "/boot/ct", "."):
        if os.path.isdir(folder):
            found = sorted(os.path.join(folder, n) for n in os.listdir(folder)
                           if n.lower().endswith(".csv") and is_recording(os.path.join(folder, n)))
            if found:
                return found
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", nargs="*", default=None, help="one or more recordings. Default: every .csv next to this file")
    ap.add_argument("--window", type=float, default=20.0, help="seconds analyzed each step")
    ap.add_argument("--hop", type=float, default=1.0, help="seconds between result lines")
    ap.add_argument("--first", type=float, default=4.0, help="first result after this many seconds")
    ap.add_argument("--loop", action="store_true", help="repeat the recordings forever")
    ap.add_argument("--once", action="store_true", help="play the recordings one time then stop")
    ap.add_argument("--fast", action="store_true", help="do not wait for real time")
    ap.add_argument("--no-trim", action="store_true", help="keep the first 2 s and last 1 s of each recording")
    a = ap.parse_args()

    paths = a.csv or find_csvs()
    paths = [p for p in paths if os.path.exists(p)]
    if not paths:
        note("ERROR no csv recording found next to this file or in /boot/ct")
        return 1
    ts, te = (0.0, 0.0) if a.no_trim else (TRIM_START_S, TRIM_END_S)
    t0 = time.monotonic()
    recs = []
    for p in paths:
        et, ev, pt, pv = load_csv(p)
        if len(et) < 1000 or len(pt) < 100:
            note("Skipped %s: not an ECG and PPG recording" % os.path.basename(p))
            continue
        if ts or te:
            et, ev, pt, pv = clip(et, ev, pt, pv, ts, te)
        recs.append((os.path.basename(p), et, ev, pt, pv))
    if not recs:
        note("ERROR no usable recording found")
        return 1
    note("CardioTwin board processor. %d recording(s): %s. Loaded in %.1f s." %
         (len(recs), ", ".join("%s %.1f s" % (r[0], r[1][-1]) for r in recs), time.monotonic() - t0))
    note("Cleaning: first %.0f s and last %.0f s of each recording skipped, beat intervals more than %d%% off the median "
         "ignored, heart rate shown only at signal quality %d or higher, shown value is the median of the last %d results."
         % (ts, te, int(REJECT_FRAC * 100), SQI_SHOW, SMOOTH_N))
    note("Source is a RECORDING processed on this board. Not live sensor data.")

    seq, loop_no, off = 0, 0, 0.0
    while True:
        for name, et, ev, pt, pv in recs:
            dur = et[-1]
            recent = []  # heart rates of the last results that passed the quality gate
            sent_e = sent_p = 0
            t = a.first
            while t <= dur + 1e-9:
                tick = time.monotonic()
                lo = max(0.0, t - a.window)
                e0, e1 = bisect.bisect_left(et, lo - 1e-7), bisect.bisect_right(et, t + 1e-7)
                p0, p1 = bisect.bisect_left(pt, lo - 1e-7), bisect.bisect_right(pt, t + 1e-7)
                r = analyze(ev[e0:e1], pv[p0:p1])
                if r["hr"] is not None and r["sqi"] >= SQI_SHOW:
                    recent = (recent + [r["hr"]])[-SMOOTH_N:]
                hs = median(recent) if (recent and r["hr"] is not None and r["sqi"] >= SQI_SHOW) else None

                # Waveform for this step: from the last sent index up to LAG_S behind the newest sample.
                e_end = max(e0, min(e1, int(round((t - LAG_S) * ECG_FS)) + 1))
                p_end = max(p0, min(p1, int(round((t - LAG_S) * PPG_FS)) + 1))
                ecg_out = ppg_out = []
                rp = pp = []
                if r["ecg"] is not None and e_end > max(sent_e, e0):
                    s = max(sent_e, e0)
                    ecg_out = [int(round(v * 1e6)) for v in r["ecg"][s - e0:e_end - e0]]
                    rp = [round(et[e0 + k], 3) for k in r["rp"] if s <= e0 + k < e_end]
                    t_ecg0 = et[s]
                    sent_e = e_end
                else:
                    t_ecg0 = et[min(sent_e, len(et) - 1)]
                if r["ppg"] is not None and p_end > max(sent_p, p0):
                    s = max(sent_p, p0)
                    ppg_out = [int(round(v * 1e6)) for v in r["ppg"][s - p0:p_end - p0]]
                    pp = [round(pt[p0 + k], 3) for k in r["pp"] if s <= p0 + k < p_end]
                    t_ppg0 = pt[s]
                    sent_p = p_end
                else:
                    t_ppg0 = pt[min(sent_p, len(pt) - 1)]
                emit({"seq": seq, "t": round(off + t, 3), "te": round(off + t_ecg0, 3), "tp": round(off + t_ppg0, 3),
                      "ecg": ecg_out, "ppg": ppg_out, "rp": [round(off + v, 3) for v in rp],
                      "pp": [round(off + v, 3) for v in pp],
                      "hr": None if r["hr"] is None else round(r["hr"], 2),
                      "hs": None if hs is None else round(hs, 2),
                      "hp": None if r["hp"] is None else round(r["hp"], 2),
                      "rm": None if r["rm"] is None else round(r["rm"], 1),
                      "cv": None if r["cv"] is None else round(r["cv"], 4),
                      "rej": r["rej"], "sqi": r["sqi"], "lab": r["lab"], "flip": r["flip"], "why": r["why"],
                      "ms": int((time.monotonic() - tick) * 1000), "loop": loop_no, "file": name, "src": "recording"})
                seq += 1
                t += a.hop
                if not a.fast:
                    wait = a.hop - (time.monotonic() - tick)
                    if wait > 0:
                        time.sleep(wait)
            off += dur + 1.0
        loop_no += 1
        if a.once or not a.loop:
            break
    note("Done.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:  # the reader closed the pipe, for example "| head -n 5"
        try:
            sys.stdout = None
        except Exception:
            pass
        os._exit(0)

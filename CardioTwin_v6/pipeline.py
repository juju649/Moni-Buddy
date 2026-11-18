"""CardioTwin signal pipeline for TI AFE4950EVM captures.

Input format (from the Track 1 manual, section 7.1): the TI GUI exports a CSV
with columns ECG_Time, ECG_Value, TIA1-3_Time, TIA1-3_Value (and others).
ECG runs at 500 Hz, PPG (TIA1-3) at 50 Hz by default. Rows end with a trailing
delimiter, so pandas needs index_col=False.

Everything here is an EXPERIMENTAL estimate. It is not a medical device and
it does not diagnose anything.
"""
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import butter, find_peaks, sosfiltfilt, welch

ECG_T, ECG_V = "ECG_Time", "ECG_Value"
PPG_T, PPG_V = "TIA1-3_Time", "TIA1-3_Value"
SQI_GOOD = 75  # score at or above this is labeled GOOD
SQI_SHOW = 75  # below this the BPM display is suppressed (was 55 before the cleanup step)
REJECT_FRAC = 0.20  # a beat interval more than 20% from the median is ignored for HR and HRV
TRIM_START_S = 2.0  # live monitor and board skip the first 2 s of a recording (filter settling)
TRIM_END_S = 1.0  # and the last 1 s


# ---------------------------------------------------------------- loading
def load_capture(path, trim=(0.0, 0.0)):
    """Return (ecg_t, ecg_v, ppg_t, ppg_v) as float arrays, time in seconds.

    trim=(start_s, end_s) drops that much from each end of the recording and restarts the clock at 0.
    The file on disk is never changed.
    """
    df = pd.read_csv(Path(path), index_col=False)
    ecg = df[[ECG_T, ECG_V]].dropna()
    ppg = df[[PPG_T, PPG_V]].dropna()
    out = []
    for frame, tc, vc in ((ecg, ECG_T, ECG_V), (ppg, PPG_T, PPG_V)):
        t, v = frame[tc].to_numpy(float), frame[vc].to_numpy(float)
        if trim[0] or trim[1]:
            keep = (t >= trim[0] - 1e-9) & (t <= t[-1] - trim[1] + 1e-9)
            t, v = t[keep] - trim[0], v[keep]
        out += [t, v]
    return tuple(out)


def sample_rate(t):
    return 1.0 / float(np.median(np.diff(t)))


# ---------------------------------------------------------------- filters
def bandpass(x, fs, lo, hi, order=3):
    hi = min(hi, 0.45 * fs)
    sos = butter(order, [lo, hi], btype="band", fs=fs, output="sos")
    return sosfiltfilt(sos, x)


# ---------------------------------------------------------------- ECG
def detect_r_peaks(ecg, fs):
    """Simple robust R-peak detector: 5-15 Hz band energy, adaptive threshold."""
    filt = bandpass(ecg, fs, 0.5, 40)
    qrs = np.abs(bandpass(ecg, fs, 5, 15))
    thr = 0.35 * np.percentile(qrs, 99)
    cand, _ = find_peaks(qrs, height=thr, distance=int(0.3 * fs))
    # Snap each candidate to the largest absolute deflection in +-60 ms.
    half = int(0.06 * fs)
    peaks = []
    for c in cand:
        a, b = max(0, c - half), min(len(filt), c + half + 1)
        peaks.append(a + int(np.argmax(np.abs(filt[a:b]))))
    peaks = np.unique(peaks)
    # Ignore the first and last 0.25 s: the band-pass filter rings at the edges and makes false beats.
    margin = int(0.25 * fs)
    peaks = peaks[(peaks >= margin) & (peaks < len(filt) - margin)]
    return filt, peaks


def rr_stats(peaks, fs):
    """Return (rr, hr, rmssd, cv, n_rejected).

    rr holds every plausible interval (30 to 200 BPM) and feeds the quality score and the irregular-rhythm
    indicator (cv). hr and rmssd use only intervals within 20% of the median, so one missed or extra
    beat does not move them.
    """
    rr = np.diff(peaks) / fs
    rr = rr[(rr > 0.3) & (rr < 2.0)]  # 30 to 200 BPM
    if len(rr) < 3:
        return rr, None, None, None, 0
    med = float(np.median(rr))
    clean = rr[np.abs(rr - med) <= REJECT_FRAC * med]
    if len(clean) < 3:
        clean = rr
    hr = 60.0 / float(np.median(clean))
    rmssd = float(np.sqrt(np.mean(np.diff(clean * 1000) ** 2))) if len(clean) > 3 else None
    cv = float(np.std(rr) / np.mean(rr))
    return rr, hr, rmssd, cv, int(len(rr) - len(clean))


# ---------------------------------------------------------------- PPG
def detect_ppg_peaks(ppg, fs):
    filt = bandpass(ppg, fs, 0.5, 4.0)
    prom = 0.3 * (np.percentile(filt, 95) - np.percentile(filt, 5))
    peaks, _ = find_peaks(filt, distance=int(0.33 * fs), prominence=max(prom, 1e-9))
    return filt, peaks


def ppg_rate(peaks, fs):
    if len(peaks) < 4:
        return None
    ibi = np.diff(peaks) / fs
    ibi = ibi[(ibi > 0.3) & (ibi < 2.0)]
    return 60.0 / float(np.median(ibi)) if len(ibi) >= 3 else None


# ---------------------------------------------------------------- respiration (experimental)
def _dominant_rate(t_events, values, lo=0.1, hi=0.5, fs_out=4.0):
    """Resample an irregular series to fs_out, band-pass, return breaths/min."""
    if len(t_events) < 8:
        return None
    t_grid = np.arange(t_events[0], t_events[-1], 1.0 / fs_out)
    if len(t_grid) < int(12 * fs_out):
        return None
    x = np.interp(t_grid, t_events, values)
    x = x - np.mean(x)
    x = bandpass(x, fs_out, lo, hi, order=2)
    f, p = welch(x, fs=fs_out, nperseg=len(x), nfft=4096)
    band = (f >= lo) & (f <= hi)
    if not band.any() or p[band].max() <= 0:
        return None
    return 60.0 * float(f[band][np.argmax(p[band])])


def respiration_estimates(ecg_t, ecg_filt, peaks, ppg_t, ppg_v, ppg_fs):
    """Return (ecg_based, ppg_based) breaths per minute. Needs >= ~20 s of data."""
    ecg_based = None
    if len(peaks) > 8:
        amp = ecg_filt[peaks]  # R-wave height modulation by breathing
        ecg_based = _dominant_rate(ecg_t[peaks], np.abs(amp))
    ppg_based = None
    if len(ppg_t) > int(20 * ppg_fs):
        slow = bandpass(ppg_v, ppg_fs, 0.1, 0.5, order=2)  # baseline wander
        f, p = welch(slow, fs=ppg_fs, nperseg=len(slow), nfft=8192)
        band = (f >= 0.1) & (f <= 0.5)
        if band.any() and p[band].max() > 0:
            ppg_based = 60.0 * float(f[band][np.argmax(p[band])])
    return ecg_based, ppg_based


# ---------------------------------------------------------------- quality
def ecg_snr_db(filt, peaks, fs):
    if len(peaks) < 3:
        return None
    mask = np.ones(len(filt), bool)
    w = int(0.08 * fs)
    for p in peaks:
        mask[max(0, p - w):p + w] = False
    noise = np.std(filt[mask]) if mask.sum() > 10 else None
    sig = np.median(np.abs(filt[peaks]))
    if not noise or noise <= 0:
        return None
    return float(20 * np.log10(sig / noise))


@dataclass
class Result:
    t_end: float = 0.0
    contact: bool = False
    hr_ecg: float = None
    hr_ppg: float = None
    rmssd_ms: float = None
    rr_cv: float = None
    resp_bpm: float = None
    resp_agree: bool = False
    snr_db: float = None
    sqi: float = 0.0
    sqi_label: str = "NO SIGNAL"
    polarity_flipped: bool = False
    rejected: int = 0  # beat intervals ignored for HR and HRV
    hr_smooth: float = None  # median of the last 5 results, set by the dashboard or the board
    reasons: list = field(default_factory=list)
    ecg_filt: np.ndarray = None
    ecg_peaks: np.ndarray = None
    ppg_filt: np.ndarray = None
    ppg_peaks: np.ndarray = None

    @property
    def hr_display(self):
        """HR shown to the user. None when the window is unreliable."""
        return self.hr_ecg if self.sqi >= SQI_SHOW else None


def analyze(ecg_t, ecg_v, ppg_t, ppg_v):
    """Analyze one window of samples and score its reliability."""
    r = Result(t_end=float(ecg_t[-1]) if len(ecg_t) else 0.0)
    if len(ecg_t) < 200:
        r.reasons.append("window too short")
        return r
    fs_e = sample_rate(ecg_t)
    span = np.ptp(ecg_v)
    if span < 1e-9 or np.std(ecg_v) < 1e-9:
        r.reasons.append("flat ECG: leads off or no signal")
        return r
    r.contact = True

    filt, peaks = detect_r_peaks(ecg_v, fs_e)
    # Leads swapped gives a flipped ECG. Heart rate is unaffected, but flip the
    # display copy so the R spikes point up.
    if len(peaks) and np.median(filt[peaks]) < 0:
        filt = -filt
        r.polarity_flipped = True
    r.ecg_filt, r.ecg_peaks = filt, peaks
    rr, r.hr_ecg, r.rmssd_ms, r.rr_cv, r.rejected = rr_stats(peaks, fs_e)

    if len(ppg_t) > 100:
        fs_p = sample_rate(ppg_t)
        r.ppg_filt, r.ppg_peaks = detect_ppg_peaks(ppg_v, fs_p)
        r.hr_ppg = ppg_rate(r.ppg_peaks, fs_p)
        eb, pb = respiration_estimates(ecg_t, filt, peaks, ppg_t, ppg_v, fs_p)
        ests = [v for v in (eb, pb) if v is not None]
        if ests:
            r.resp_bpm = float(np.mean(ests))
            r.resp_agree = len(ests) == 2 and abs(eb - pb) <= 3.0

    r.snr_db = ecg_snr_db(filt, peaks, fs_e)

    # Signal quality index: four checks worth 25 points each.
    score = 0.0
    if r.hr_ecg is None:
        r.reasons.append("too few valid beats")
    else:
        score += 25
        within = np.mean(np.abs(rr - np.median(rr)) < 0.3 * np.median(rr))
        score += 25 * float(within)
        if within < 0.8:
            r.reasons.append("irregular beat timing or missed beats")
    if r.snr_db is not None:
        score += 25 * float(np.clip((r.snr_db - 6) / 14, 0, 1))  # 6 dB ->0, 20 dB ->full
        if r.snr_db < 10:
            r.reasons.append("noisy ECG")
    if r.hr_ecg and r.hr_ppg:
        if abs(r.hr_ecg - r.hr_ppg) <= 0.1 * r.hr_ecg:
            score += 25
        else:
            r.reasons.append("ECG and PPG rates disagree")
    elif r.hr_ecg:
        score += 10  # no PPG to cross-check
    r.sqi = round(score, 1)
    r.sqi_label = "GOOD" if score >= SQI_GOOD else "FAIR" if score >= SQI_SHOW else "POOR"
    return r

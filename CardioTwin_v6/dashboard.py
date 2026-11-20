"""CardioTwin dashboard. Run with:  python -m streamlit run dashboard.py

Tabs: Live monitor, Recording review, SK-AM62B board (with backend and ESP32 watch links), Connect AFE, Moni-Buddy export, About.
EXPERIMENTAL student prototype. Not a medical device. Does not diagnose anything.
"""
import json
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import afe_serial as afe
import pipeline as p
import pushevents as pe
from board_link import BoardLink
from bridge_out import Forwarder, WatchBridge
from source import LiveSource, ReplaySource
from twin import Twin

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
ANALYSIS_WINDOW_S = 20   # seconds of signal given to the pipeline (same as the board)
PLOT_WINDOW_S = 10       # seconds of waveform drawn

TEAL, NAVY, ORANGE = "#0e7c86", "#1f3a5f", "#f08a24"
INK, MUTED = "#10202b", "#5a6772"

st.set_page_config(page_title="CardioTwin", page_icon="❤️", layout="wide")

CSS = f"""
<style>
.block-container {{padding-top: 3.6rem; padding-bottom: 2rem; max-width: 1400px;}}
h1, h2, h3 {{color: {INK};}}
.ct-head {{display:flex; align-items:center; justify-content:space-between; margin-bottom:6px;}}
.ct-title {{font-size:34px; font-weight:800; color:{INK}; letter-spacing:-0.02em; line-height:1.1;}}
.ct-sub {{font-size:15px; color:{MUTED}; margin-top:2px;}}
.pill {{display:inline-block; padding:6px 14px; border-radius:999px; font-size:14px; font-weight:700;}}
.pill.live {{background:#fde4e0; color:#a1281a;}}
.pill.replay {{background:#e3f1f2; color:#0b5e66;}}
.pill.idle {{background:#eceff1; color:#4a5560;}}
.card {{background:#ffffff; border:1px solid #e1e7ea; border-radius:14px; padding:14px 16px; height:100%;}}
.card .lbl {{font-size:12px; font-weight:700; letter-spacing:0.07em; text-transform:uppercase; color:{MUTED};}}
.card .val {{font-size:clamp(20px, 2vw, 34px); font-weight:800; color:{INK}; line-height:1.15; margin-top:2px; white-space:nowrap;}}
.card .unit {{white-space:nowrap; font-size:15px; font-weight:600; color:{MUTED}; margin-left:4px;}}
.card .sub {{font-size:13px; margin-top:2px; font-weight:600;}}
.good {{color:#17803d;}} .warn {{color:#b45f06;}} .bad {{color:#b42318;}} .mute {{color:{MUTED};}}
.strip {{border-radius:12px; padding:12px 16px; font-size:15px; font-weight:600; margin:10px 0 6px 0;}}
.strip.ok {{background:#e6f4ea; color:#14532d; border:1px solid #bfe3c9;}}
.strip.alert {{background:#fff4e0; color:#6b3d00; border:1px solid #f3d6a1;}}
.strip.info {{background:#e8f1f9; color:#16405e; border:1px solid #c5dcf0;}}
[data-stale="true"] {{opacity:1 !important; transition:none !important;}}
.stPlotlyChart, .element-container {{transition:none !important; animation:none !important;}}
.note {{font-size:13px; color:{MUTED};}}
.disclaimer {{font-size:13px; color:{MUTED}; border-left:3px solid {TEAL}; padding:4px 10px; margin:4px 0 14px 0;}}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------- helpers
@st.cache_resource
def get_stream():
    return afe.AFEStream()


@st.cache_resource
def get_link():
    return BoardLink()


@st.cache_resource
def get_forwarder():
    return Forwarder(get_link())


@st.cache_resource
def get_watch():
    return WatchBridge(get_link())


def laptop_ip():
    """Best guess of this laptop's address on the Wi-Fi or hotspot, for the ESP32 watch."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("10.255.255.255", 1))
            return sk.getsockname()[0]
    except Exception:
        return "YOUR_LAPTOP_IP"


class BoardName:
    """Gives the monitor a status line for the board link."""

    def __init__(self, link):
        self.link = link

    @property
    def name(self):
        l = self.link
        where = "the SK-AM62B board" if l.kind == "serial" else "a simulated board on this laptop"
        ms = "" if l.proc_ms is None else f" The board needs {l.proc_ms} ms per one-second step."
        fl = "" if not l.file else f" Recording: {l.file}."
        return f"Results processed on {where}.{ms}{fl} The board is replaying a recorded capture, not live sensors"


def card(label, value, unit="", sub="", tone="mute"):
    unit_html = f'<span class="unit">{unit}</span>' if unit else ""
    sub_html = f'<div class="sub {tone}">{sub}</div>' if sub else '<div class="sub">&nbsp;</div>'
    return f'<div class="card"><div class="lbl">{label}</div><div class="val">{value}{unit_html}</div>{sub_html}</div>'


def fmt(v, digits=0):
    return "--" if v is None else f"{v:.{digits}f}"


def quality_tone(label):
    return {"GOOD": "good", "FAIR": "warn", "POOR": "bad"}.get(label, "mute")


def style_fig(fig, height):
    fig.update_layout(template="plotly_white", height=height, margin=dict(l=10, r=10, t=34, b=10),
                      showlegend=False, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#ffffff",
                      font=dict(family="Inter, Segoe UI, sans-serif", size=13, color=INK))
    fig.update_xaxes(gridcolor="#edf1f3", zeroline=False)
    fig.update_yaxes(gridcolor="#edf1f3", zeroline=False)
    return fig


def header(source_kind):
    pill = {"live": '<span class="pill live">● LIVE</span>',
            "replay": '<span class="pill replay">▶ REPLAY OF RECORDING</span>',
            "board": '<span class="pill live">● PROCESSED ON SK-AM62B</span>',
            "boardsim": '<span class="pill replay">◐ SIMULATED BOARD (THIS LAPTOP)</span>',
            "idle": '<span class="pill idle">○ NOT CONNECTED</span>'}[source_kind]
    st.markdown(f'<div class="ct-head"><div><div class="ct-title">CardioTwin</div>'
                f'<div class="ct-sub">Edge digital twin for ECG and PPG · Team MoniBuddy</div></div>'
                f'<div>{pill}</div></div>', unsafe_allow_html=True)
    st.markdown('<div class="disclaimer">Experimental student prototype. Not a medical device. '
                'It does not diagnose, treat, or screen for any condition.</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------- monitor
def init_source(mode, csv_path):
    if mode == "replay":
        st.session_state.src = ReplaySource(csv_path)
    else:
        st.session_state.src = LiveSource(get_stream())
    st.session_state.twin = Twin(DATA / "twin")
    st.session_state.key = (mode, str(csv_path))


def step(src, twin, dt):
    """Advance the source, analyze the newest window, update the twin."""
    if not src.at_end:
        src.advance(dt)
    ecg_t, ecg_v, ppg_t, ppg_v = src.window(ANALYSIS_WINDOW_S)
    res = p.analyze(ecg_t, ecg_v, ppg_t, ppg_v)
    summary = twin.update(res)
    recent = [x["hr"] for x in list(twin.history)[-5:] if x["hr"] is not None]
    res.hr_smooth = float(np.median(recent)) if (recent and res.hr_display is not None) else None
    return res, summary, (ecg_t, ecg_v, ppg_t, ppg_v)


def waveform_fig(res, raw):
    ecg_t, ecg_v, ppg_t, ppg_v = raw
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1,
                        subplot_titles=("ECG (band-pass 0.5 to 40 Hz)", "PPG pulse wave (band-pass 0.5 to 4 Hz)"))
    if res.ecg_filt is not None:
        fig.add_trace(go.Scatter(x=ecg_t, y=res.ecg_filt, line=dict(width=1.3, color=TEAL)), 1, 1)
        pk = res.ecg_peaks
        fig.add_trace(go.Scatter(x=ecg_t[pk], y=res.ecg_filt[pk], mode="markers",
                                 marker=dict(size=8, color=ORANGE)), 1, 1)
    if res.ppg_filt is not None:
        fig.add_trace(go.Scatter(x=ppg_t, y=res.ppg_filt, line=dict(width=2, color=NAVY)), 2, 1)
        pk = res.ppg_peaks
        fig.add_trace(go.Scatter(x=ppg_t[pk], y=res.ppg_filt[pk], mode="markers",
                                 marker=dict(size=8, color=ORANGE)), 2, 1)
    if len(ecg_t):
        fig.update_xaxes(range=[max(0, ecg_t[-1] - PLOT_WINDOW_S), ecg_t[-1]], row=2, col=1,
                         title_text="Recording time (s)")
    return style_fig(fig, 470)


def trend_fig(history, baseline):
    h = [s for s in history if s["hr"] is not None]
    fig = go.Figure()
    if h:
        fig.add_trace(go.Scatter(x=[s["t"] for s in h], y=[s["hr"] for s in h], mode="lines",
                                 line=dict(width=2.5, color=TEAL), fill="tozeroy",
                                 fillcolor="rgba(14,124,134,0.08)"))
        lo, hi = min(s["hr"] for s in h), max(s["hr"] for s in h)
        fig.update_yaxes(range=[max(30, lo - 15), hi + 15])
    if baseline:
        fig.add_hline(y=baseline, line=dict(color=ORANGE, width=1.5, dash="dot"),
                      annotation_text=f"baseline {baseline:.0f}", annotation_position="top left")
    fig.update_layout(title=dict(text="Heart-rate trend (BPM, good-quality windows)", font=dict(size=14)))
    fig.update_xaxes(title_text="Recording time (s)")
    return style_fig(fig, 250)


def render_monitor(res, summary, raw, src, twin):
    c = st.columns(5)
    hr_show = res.hr_smooth if (res.hr_smooth is not None and res.hr_display is not None) else res.hr_display
    c[0].markdown(card("Heart rate", fmt(hr_show), "BPM",
                       "median of last 5 results" if hr_show else "waiting for a clean signal",
                       "good" if hr_show else "mute"), unsafe_allow_html=True)
    c[1].markdown(card("HRV (RMSSD)", fmt(res.rmssd_ms), "ms",
                       f"{res.rejected} odd beat(s) ignored" if res.rejected else "beat-to-beat variation"),
                  unsafe_allow_html=True)
    c[2].markdown(card("Breathing", fmt(res.resp_bpm), "/min", "experimental estimate",
                       "warn" if res.resp_bpm else "mute"), unsafe_allow_html=True)
    c[3].markdown(card("Signal quality", f"{res.sqi:.0f}", "/ 100", res.sqi_label,
                       quality_tone(res.sqi_label)), unsafe_allow_html=True)
    c[4].markdown(card("PPG heart rate", fmt(res.hr_ppg), "BPM", "independent check"), unsafe_allow_html=True)

    span = float(raw[0][-1] - raw[0][0]) if len(raw[0]) else 0.0
    if span < 5:
        st.markdown('<div class="strip info">Collecting the first 5 seconds of signal.</div>', unsafe_allow_html=True)
    elif summary["alerts"]:
        txt = "<br>".join(f"Experimental indicator: {a}" for a in summary["alerts"])
        st.markdown(f'<div class="strip alert">{txt}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="strip ok">No indicators raised in this window.</div>', unsafe_allow_html=True)
    notes = []
    if res.polarity_flipped:
        notes.append("ECG polarity auto-corrected for display. Heart rate is unaffected.")
    if res.reasons:
        notes.append("Quality notes: " + "; ".join(res.reasons))
    clean = ("Cleaning: " + ("" if getattr(src, "live", False) else "first 2 s and last 1 s of the recording skipped, ")
             + "beat intervals more than 20% off the median ignored, heart rate shown only at signal quality 75 or higher, "
             "shown value is the median of the last 5 results. The raw file is never changed.")
    st.markdown(f'<div class="note">{src.name}. {" ".join(notes)}<br>{clean}</div>', unsafe_allow_html=True)

    left, right = st.columns([3, 2])
    with left:
        st.plotly_chart(waveform_fig(res, raw), width="stretch")
    with right:
        st.plotly_chart(trend_fig(twin.history, twin.baseline_hr), width="stretch")
        d = st.columns(3)
        d[0].markdown(card("Baseline HR", fmt(twin.baseline_hr), "BPM", "learned from good windows"), unsafe_allow_html=True)
        d[1].markdown(card("Baseline HRV", fmt(twin.baseline_rmssd), "ms"), unsafe_allow_html=True)
        d[2].markdown(card("Waiting to sync", str(twin.queue_depth()), "",
                           "online" if twin.online else "offline, queueing",
                           "good" if twin.online else "warn"), unsafe_allow_html=True)

    with st.expander("Recent summaries from the digital twin"):
        rows = [{"recording time (s)": s["t"], "HR (BPM)": s["hr"], "HRV (ms)": s["hrv_rmssd_ms"],
                 "quality": f'{s["sqi"]:.0f} {s["quality"]}', "indicators": "; ".join(s["alerts"]) or "none"}
                for s in list(twin.history)[-12:][::-1]]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def monitor_tab(mode, csv_choice, speed, loop):
    stream = get_stream()
    link = get_link()
    board_mode = mode in ("board", "boardsim")
    key = (mode, str(DATA / csv_choice) if mode == "replay" else "live")
    if st.session_state.get("key") != key:
        if board_mode:
            st.session_state.src = None
            st.session_state.twin = Twin(DATA / "twin")
            st.session_state.key = key
        else:
            init_source(mode, DATA / csv_choice)
    src, twin = st.session_state.src, st.session_state.twin
    twin.online = not st.sidebar.checkbox("Simulate network loss", value=False)
    if twin.online and st.sidebar.button("Sync now (stub cloud)"):
        n = twin.drain_queue(lambda rows: True)  # replace with an HTTPS POST to your server
        st.sidebar.write(f"Sent {n} summaries")
    if mode == "replay" and st.sidebar.button("Restart replay"):
        init_source(mode, DATA / csv_choice)
        src, twin = st.session_state.src, st.session_state.twin

    @st.fragment(run_every=2.0)
    def live():
        if board_mode:
            if link.state not in ("receiving", "stale") or link.frames == 0:
                st.markdown(f'<div class="strip info">Board link: {link.state}. {link.message} '
                            'Open the "SK-AM62B board" tab to connect.</div>', unsafe_allow_html=True)
                return
            if link.state == "stale":
                st.markdown(f'<div class="strip alert">{link.message}</div>', unsafe_allow_html=True)
            res, raw = link.result()
            summary = twin.update(res)
            render_monitor(res, summary, raw, BoardName(link), twin)
            return
        if mode == "live" and stream.state not in ("streaming", "calibrating"):
            st.markdown(f'<div class="strip info">Live AFE is not streaming yet ({stream.state}: {stream.message}). '
                        'Open the "Connect AFE" tab, plug in the AFE and press Start.</div>', unsafe_allow_html=True)
            return
        if mode == "live" and stream.state == "calibrating":
            st.markdown(f'<div class="strip info">{stream.message} '
                        f'({stream.words_total} words received)</div>', unsafe_allow_html=True)
            return
        if src.at_end and loop:
            src.restart()
        res, summary, raw = step(src, twin, float(speed) * 2)
        render_monitor(res, summary, raw, src, twin)

    live()


# ---------------------------------------------------------------- review
@st.cache_resource
def load_review(csv_path):
    import analyze_signals as a
    et, ev, pt, pv = p.load_capture(Path(csv_path))
    res = p.analyze(et, ev, pt, pv)
    metrics, _ = a.analyze_file(Path(csv_path))
    return et, ev, pt, pv, res, metrics


def review_tab(csv_path):
    et, ev, pt, pv, res, m = load_review(str(csv_path))
    duration = float(et[-1] - et[0])
    c = st.columns(5)
    c[0].markdown(card("Heart rate (ECG)", fmt(m["heart_rate_ecg_bpm"]), "BPM"), unsafe_allow_html=True)
    c[1].markdown(card("Heart rate (PPG)", fmt(m["heart_rate_ppg_bpm"]), "BPM"), unsafe_allow_html=True)
    c[2].markdown(card("Capture duration", f"{duration:.1f}", "s"), unsafe_allow_html=True)
    c[3].markdown(card("Sample rate ECG / PPG", f"{m['ecg_sample_rate_hz']:.0f} / {m['ppg_sample_rate_hz']:.0f}", "Hz"),
                  unsafe_allow_html=True)
    c[4].markdown(card("Signal quality", f"{m['signal_quality']:.0f}", "/ 100", m["signal_quality_label"],
                       quality_tone(m["signal_quality_label"])), unsafe_allow_html=True)
    st.markdown(f'<div class="note">{m["ecg_beats"]} ECG beat candidates, {m["ppg_peaks"]} pulse peak candidates, '
                f'{m["ecg_ppg_matches"]} candidate ECG/PPG matches. Candidate matches are internal events, '
                'not validated pulse transit time.</div>', unsafe_allow_html=True)

    win = st.slider("Viewing window (s)", 2, int(min(30, max(3, duration))), 10, key="rv_win")
    start = st.slider("Navigate recording (s)", 0.0, max(0.0, duration - win), 0.0, 0.5, key="rv_start")
    k1, k2 = st.columns(2)
    filtering = k1.toggle("Digital filtering", value=True, key="rv_filter")
    markers = k2.toggle("R-peak markers", value=True, key="rv_peaks")

    t0 = float(et[0])
    lo, hi = t0 + start, t0 + start + win
    me, mp = (et >= lo) & (et <= hi), (pt >= lo) & (pt <= hi)
    ecg_y = res.ecg_filt if filtering else (ev - np.mean(ev))
    ppg_y = res.ppg_filt if filtering else (pv - np.mean(pv))
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1,
                        subplot_titles=("ECG" + (" (band-pass 0.5 to 40 Hz)" if filtering else " (raw, mean removed)"),
                                        "PPG" + (" (band-pass 0.5 to 4 Hz)" if filtering else " (raw, mean removed)")))
    fig.add_trace(go.Scatter(x=et[me] - t0, y=ecg_y[me], line=dict(width=1.3, color=TEAL)), 1, 1)
    fig.add_trace(go.Scatter(x=pt[mp] - t0, y=ppg_y[mp], line=dict(width=2, color=NAVY)), 2, 1)
    if markers:
        rp = res.ecg_peaks[(et[res.ecg_peaks] >= lo) & (et[res.ecg_peaks] <= hi)]
        fig.add_trace(go.Scatter(x=et[rp] - t0, y=ecg_y[rp], mode="markers", marker=dict(size=8, color=ORANGE)), 1, 1)
        pp = res.ppg_peaks[(pt[res.ppg_peaks] >= lo) & (pt[res.ppg_peaks] <= hi)]
        fig.add_trace(go.Scatter(x=pt[pp] - t0, y=ppg_y[pp], mode="markers", marker=dict(size=8, color=ORANGE)), 2, 1)
    fig.update_xaxes(title_text="Time (s)", row=2, col=1)
    st.plotly_chart(style_fig(fig, 540), width="stretch")
    st.markdown('<div class="note">This view shows a stored recording. Moving a slider does not take a new measurement.</div>',
                unsafe_allow_html=True)


# ---------------------------------------------------------------- hardware
def hardware_tab():
    stream = get_stream()
    st.markdown("#### Connect the AFE4950EVM over USB")
    st.markdown(
        "1. **Prime the AFE once.** Open the TI Bio-Sensing GUI, import your settings file, start one capture, "
        "then **close the GUI** so the serial port is free.\n"
        "2. Plug the AFE micro-USB cable into this computer. Put the ECG electrodes and PPG sensor on.\n"
        "3. Press **Scan ports**, then **Start live capture**. Switch the sidebar to *Live AFE4950 (USB)* to watch it on the monitor.")
    b = st.columns(3)
    if b[0].button("Scan ports"):
        st.session_state.scan = afe.list_serial_ports()
    if b[1].button("Test AFE ports"):
        port, report = afe.find_capture_port()
        st.session_state.probe = (port, report)
    if "scan" in st.session_state:
        st.dataframe(pd.DataFrame(st.session_state.scan), hide_index=True, width="stretch")
    if "probe" in st.session_state:
        port, report = st.session_state.probe
        (st.success if port else st.warning)(" | ".join(report) + (f"  → capture port: {port}" if port else ""))

    with st.expander("Advanced settings"):
        ports = ["Auto-detect"] + [r["port"] for r in afe.list_serial_ports()]
        port_choice = st.selectbox("Capture port", ports)
        fifo_file = st.file_uploader("FIFO sequence file (*_FIFO_SEQ.cfg, optional)", type=["cfg", "txt"])
        slot = st.number_input("PPG slot in the 11-word cycle (-1 = find it automatically)", -1, 10, -1)

    s1, s2, s3 = st.columns(3)
    if s1.button("Start live capture", type="primary"):
        fifo_seq = None
        if fifo_file is not None:
            fifo_seq = [ln.strip() for ln in fifo_file.getvalue().decode().splitlines() if ln.strip()]
        stream.start(port=None if port_choice == "Auto-detect" else port_choice, fifo_seq=fifo_seq,
                     ppg_slot=None if slot < 0 else int(slot))
    if s2.button("Stop"):
        stream.stop()
    if s3.button("Save capture as CSV"):
        out = DATA / f"live_capture_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        n_e, n_p = stream.save_csv(out)
        st.success(f"Saved {n_e} ECG and {n_p} PPG samples to {out.name}. It now appears in the recording list.")

    @st.fragment(run_every=1.0)
    def status():
        tone = {"streaming": "good", "calibrating": "warn", "error": "bad"}.get(stream.state, "mute")
        d = st.columns(4)
        d[0].markdown(card("State", stream.state.upper(), "", stream.message[:70], tone), unsafe_allow_html=True)
        d[1].markdown(card("Port", stream.port or "--", "", f"start cmd {stream.variant or '--'}"), unsafe_allow_html=True)
        d[2].markdown(card("Data rate", f"{stream.rate:.0f}", "words/s", "expect about 550"), unsafe_allow_html=True)
        d[3].markdown(card("Lines", str(stream.lines), "", f"{stream.bad_lines} not parsed",
                           "bad" if stream.bad_lines > 20 else "mute"), unsafe_allow_html=True)
        if stream.raw_tail:
            st.markdown("**Latest raw lines from the AFE**")
            st.code("\n".join(list(stream.raw_tail)[-8:]), language="text")
    status()

    with st.expander("If it does not stream"):
        st.markdown(
            "- **No port found:** the TI GUI must be closed. Unplug and replug the micro-USB cable. Use a data cable.\n"
            "- **No data after the start command:** the AFE has no register setup. Prime it with the GUI first, "
            "close the GUI, and do not power-cycle the EVM.\n"
            "- **Stuck on calibrating:** the electrodes need good contact so the app finds the PPG slot. "
            "Load the *_FIFO_SEQ.cfg* file from the GUI export, or set the PPG slot by hand.\n"
            "- On the SK-AM62B (Linux) the same code works. The AFE shows up as /dev/ttyACM0 and /dev/ttyACM1.")


# ---------------------------------------------------------------- board
def board_tab():
    link = get_link()
    st.markdown("#### Process the recording on the SK-AM62B, then show it here")
    st.markdown(
        "The board runs `board_processor.py`. It filters the ECG and PPG, finds the heartbeats, scores the signal "
        "and prints one finished result per second. This dashboard only reads and draws those results. "
        "It needs no Ethernet. It uses the debug micro-USB cable you already use for the console.")
    st.markdown("**Set up the board once**")
    st.markdown(
        "1. Power off the board. Put its microSD card in your laptop. Open the small partition that Windows shows.\n"
        "2. Make a folder named `ct` there. Copy `board_processor.py` and `data/AFE4950_CAPTURED_DATA.csv` into it.\n"
        "3. Put the card back, power the board, and log in on the console (PuTTY, COM11, 115200).\n"
        "4. Test it on the board:")
    st.code("python3 /boot/ct/board_processor.py --once --fast | head -n 5", language="bash")
    st.markdown("You should see lines that start with `CT1`. Then start it for the dashboard:")
    st.code("python3 /boot/ct/board_processor.py --loop > $(tty) 2>&1 &", language="bash")
    st.markdown("5. **Close PuTTY.** Windows lets only one program use a COM port. Pick the port below and press Connect.")

    ports = [r["port"] for r in afe.list_serial_ports()]
    c = st.columns([2, 1, 1, 2])
    port = c[0].selectbox("Console port (the COM port that showed the login prompt)", ports if ports else ["no ports found"])
    if c[1].button("Connect to board", type="primary", key="brd_connect") and ports:
        link.start_serial(port)
    if c[2].button("Stop", key="brd_stop"):
        link.stop()
    if c[3].button("Run a simulated board on this laptop", key="brd_sim"):
        link.start_simulated()
    st.markdown('<div class="note">The simulated board runs the same board_processor.py on this laptop. '
                'It proves the dashboard works. The header then says SIMULATED, not SK-AM62B.</div>',
                unsafe_allow_html=True)

    @st.fragment(run_every=1.0)
    def status():
        tone = {"receiving": "good", "waiting": "warn", "stale": "warn", "error": "bad"}.get(link.state, "mute")
        d = st.columns(4)
        d[0].markdown(card("Link", link.state.upper(), "", (link.message or "")[:80], tone), unsafe_allow_html=True)
        d[1].markdown(card("Source", link.port or "--", "", link.kind or ""), unsafe_allow_html=True)
        d[2].markdown(card("Results received", str(link.frames), "", f"{link.bad_lines} damaged lines skipped",
                           "bad" if link.bad_lines > 10 else "mute"), unsafe_allow_html=True)
        d[3].markdown(card("Board time per step", "--" if link.proc_ms is None else str(link.proc_ms), "ms",
                           "must stay under 1000"), unsafe_allow_html=True)
        if link.raw_tail:
            st.markdown("**Latest lines from the board**")
            st.code("\n".join(list(link.raw_tail)[-6:]), language="text")
    status()
    st.markdown("Then pick **SK-AM62B board** in the sidebar to watch the results on the Live monitor.")

    fw, wb = get_forwarder(), get_watch()
    with st.expander("Send the board's results to the Moni-Buddy backend (your friends' software)"):
        st.markdown(
            "Every few seconds this sends one event to the backend: heart rate, state, risk, time. "
            "The backend saves it in Supabase and your friend's dashboard shows it. "
            "Events that cannot be sent wait in a queue and go out when the backend is back.")
        f1 = st.columns([3, 1, 1, 2])
        url = f1[0].text_input("Backend address", "http://localhost:8000", key="fw_url")
        patient = f1[1].text_input("Patient id", "p1", key="fw_patient")
        every = f1[2].number_input("Every (s)", 2.0, 60.0, 5.0, key="fw_every")
        header = f1[3].text_input("Extra header (optional)", "", key="fw_header",
                                  placeholder="Authorization: Bearer KEY")
        b1 = st.columns(3)
        if b1[0].button("Start forwarding", type="primary", key="fw_start"):
            hdr = {}
            if ":" in header:
                k, _, v = header.partition(":")
                hdr[k.strip()] = v.strip()
            fw.start(url, "/events", patient, hdr, every)
        if b1[1].button("Stop forwarding", key="fw_stop"):
            fw.stop()
        if b1[2].button("Send one SIMULATED alert", key="fw_sim"):
            try:
                st.success(f"Simulated alert sent (HTTP {fw.send_simulated_alert()}). Tell your audience it is simulated.")
            except Exception as e:
                st.error(f"Could not send: {e}")
        st.markdown('<div class="note">The simulated alert is a fixed high-heart-rate event so the alert banner can be '
                    'shown. The backend cannot tell it is simulated, so say so when you show it.</div>',
                    unsafe_allow_html=True)

        @st.fragment(run_every=1.0)
        def fw_status():
            tone = {"running": "good", "error": "bad"}.get(fw.state, "mute")
            d = st.columns(4)
            d[0].markdown(card("Forwarder", fw.state.upper(), "", fw.message[:80], tone), unsafe_allow_html=True)
            d[1].markdown(card("Events sent", str(fw.sent), "", f"last reply HTTP {fw.last_http}" if fw.last_http else ""),
                          unsafe_allow_html=True)
            d[2].markdown(card("Waiting to send", str(fw.waiting), "", "queued on disk",
                               "warn" if fw.waiting else "mute"), unsafe_allow_html=True)
            le = fw.last_event
            d[3].markdown(card("Last event", "--" if not le else f"{le['hr']}", "BPM" if le else "",
                               "" if not le else f"{le['state']}, risk {le['risk']}"), unsafe_allow_html=True)
        fw_status()

    with st.expander("Show results on the ESP32 watch"):
        st.markdown(
            "Two ways to reach the watch. **Wi-Fi:** the watch asks this laptop for the newest result once a second. "
            "**USB cable:** this laptop writes one text line per second to the watch's serial port. "
            "Both send only the finished numbers: heart rate, state, risk and signal quality.")
        ip = laptop_ip()
        w1 = st.columns([1, 2, 2])
        wport = w1[0].number_input("Port", 1024, 65000, 8080, key="wb_port")
        if w1[1].button("Start watch server (Wi-Fi)", key="wb_http"):
            wb.start_http(int(wport))
        if w1[2].button("Stop watch server", key="wb_http_stop"):
            wb.stop_http()
        st.code(f"http://{ip}:{int(wport)}/latest.txt", language="text")
        st.markdown('<div class="note">Put the watch and this laptop on the same Wi-Fi or phone hotspot. '
                    'Open that address in a browser first. A line like 94,normal,0.10,98,1 means it works. '
                    'The fields are heart rate, state, risk, signal quality, valid.</div>', unsafe_allow_html=True)
        w2 = st.columns([2, 2, 2])
        ser_ports = [r["port"] for r in afe.list_serial_ports()]
        wser = w2[0].selectbox("Watch USB port", ser_ports if ser_ports else ["no ports found"], key="wb_ser")
        if w2[1].button("Start USB output", key="wb_ser_go") and ser_ports:
            wb.start_serial(wser)
        if w2[2].button("Stop USB output", key="wb_ser_stop"):
            wb.stop_serial()

        @st.fragment(run_every=1.0)
        def wb_status():
            d = st.columns(3)
            d[0].markdown(card("Wi-Fi server", wb.http_state[:24].upper(), "", f"port {wb.http_port}" if wb.http_port else ""),
                          unsafe_allow_html=True)
            d[1].markdown(card("Requests from the watch", str(wb.requests), ""), unsafe_allow_html=True)
            d[2].markdown(card("USB output", wb.serial_state[:24].upper(), "", f"{wb.lines_written} lines written"),
                          unsafe_allow_html=True)
        wb_status()
    with st.expander("If nothing arrives"):
        st.markdown(
            "- **Could not open the port:** close PuTTY and any other serial program.\n"
            "- **Link stays WAITING:** the board is not running the program. Log in again and rerun the `--loop` command.\n"
            "- **Damaged lines:** the Linux console also prints its own messages. A few are normal.\n"
            "- **Wrong port:** try the other COM ports. The Linux console is the one that showed the login prompt.")


# ---------------------------------------------------------------- export
def export_tab(csvs):
    st.markdown("#### Send results to the Moni-Buddy dashboard")
    st.markdown('<div class="note">Turns a recording into events in the format the Moni-Buddy backend expects. '
                'Each event comes from a 20-second window. Unreliable windows are skipped.</div>', unsafe_allow_html=True)
    c = st.columns(4)
    name = c[0].selectbox("Recording", csvs, key="ex_csv")
    patient = c[1].text_input("Patient id", "p1")
    window = c[2].number_input("Window (s)", 5.0, 60.0, 20.0)
    stretch = c[3].number_input("Time stretch (30 spreads 1 min over 30 min)", 1.0, 300.0, 30.0)
    demo = st.checkbox("Add one SIMULATED alert (marked as simulated, never posted)", value=True)
    if st.button("Generate events", type="primary"):
        path = DATA / name
        et, *_ = p.load_capture(path)
        start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=float(et[-1]) * stretch)
        events = pe.build_events(path, patient, window, 5.0, start, stretch)
        real = len(events)
        if demo and events:
            last = events[-1]
            t = datetime.strptime(last["timestamp"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(seconds=5 * stretch)
            events.append({"event_id": "simulated-alert", "patient_id": patient, "state": "elevated_hr",
                           "risk": 0.82, "hr": 128, "timestamp": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                           "acknowledged_by": None, "acknowledged_at": None, "source": "simulated"})
        st.session_state.events = (events, real)
    if "events" in st.session_state:
        events, real = st.session_state.events
        st.success(f"{real} real events" + (" and 1 simulated alert" if len(events) > real else ""))
        st.dataframe(pd.DataFrame(events)[["timestamp", "state", "hr", "risk"] +
                                          (["source"] if any("source" in e for e in events) else [])],
                     hide_index=True, width="stretch")
        st.download_button("Download events.json", json.dumps(events, indent=1), "events.json", "application/json")
        url = st.text_input("Backend URL to POST real events to (optional)", "http://localhost:8000")
        if st.button("POST real events to backend"):
            try:
                ok = sum(1 for e in events[:real] if pe.post(url, e) in (200, 201))
                st.success(f"Posted {ok} of {real} events to {url}")
            except Exception as e:
                st.error(f"Could not reach {url}: {e}")


# ---------------------------------------------------------------- about
def about_tab():
    st.markdown("""
#### What this is
CardioTwin reads ECG and PPG from a TI AFE4950EVM, cleans the signals, finds each heartbeat, and keeps a running
**digital twin**: your baseline heart rate and variability, a short history, and a queue of summaries that waits
when the network drops. A dashboard shows the result and flags changes.

#### How it works
- ECG is band-passed 0.5 to 40 Hz. R-peaks come from 5 to 15 Hz band energy. PPG is band-passed 0.5 to 4 Hz.
- Heart rate is the median beat interval. HRV is RMSSD. Breathing is an experimental estimate.
- A signal quality score (0 to 100) checks beat count, regularity, noise and ECG to PPG agreement.
  Readings are hidden below 75.
- Indicators fire for heart rate above 120 or below 45 BPM, 25% away from baseline, or irregular beat timing.

#### Honest limits
- It is not a medical device and it does not diagnose.
- SpO2 and stress are not computed. The sensor setup uses one light color.
- The live USB reader follows TI's own capture script and passes tests against a simulated AFE.
  It is not yet tested on the real board.
- No validation against a pulse oximeter has been done.
""")


# ---------------------------------------------------------------- main
def main():
    csvs = sorted(c.name for c in DATA.glob("*.csv"))
    if not csvs:
        st.info("Put a TI GUI export in the data folder as AFE4950_CAPTURED_DATA.csv, or run: python make_demo_csv.py")
        return

    st.sidebar.markdown("### Data source")
    labels = {"Recorded capture (replay stream)": "replay", "Live AFE4950 (USB)": "live",
              "SK-AM62B board (processed on the board)": "board",
              "Simulated board (on this laptop)": "boardsim"}
    mode_label = st.sidebar.radio("Monitor shows", list(labels), label_visibility="collapsed")
    mode = labels[mode_label]
    default = csvs.index("AFE4950_CAPTURED_DATA.csv") if "AFE4950_CAPTURED_DATA.csv" in csvs else 0
    csv_choice, speed, loop = csvs[default], 1, True
    if mode == "replay":
        csv_choice = st.sidebar.selectbox("Recording", csvs, index=default)
        speed = st.sidebar.slider("Replay speed (x real time)", 1, 10, 1)
        loop = st.sidebar.checkbox("Loop recording", value=True)
    stream = get_stream()
    link = get_link()
    if mode in ("board", "boardsim"):
        header(mode if link.state in ("receiving", "stale") else "idle")
    else:
        header("live" if (mode == "live" and stream.state == "streaming") else ("replay" if mode == "replay" else "idle"))

    # Embedded in the Moni-Buddy web dashboard (?view=monibuddy): only the live monitor and board tabs.
    if st.query_params.get("view") == "monibuddy":
        t_mon, t_brd = st.tabs(["Live monitor", "SK-AM62B board"])
        with t_mon:
            monitor_tab(mode, csv_choice, speed, loop)
        with t_brd:
            board_tab()
        return

    t_mon, t_rev, t_brd, t_hw, t_exp, t_about = st.tabs(
        ["Live monitor", "Recording review", "SK-AM62B board", "Connect AFE", "Moni-Buddy export", "About"])
    with t_mon:
        monitor_tab(mode, csv_choice, speed, loop)
    with t_rev:
        review_tab(DATA / (csv_choice if mode == "replay" else csvs[default]))
    with t_brd:
        board_tab()
    with t_hw:
        hardware_tab()
    with t_exp:
        export_tab(csvs)
    with t_about:
        about_tab()


main()

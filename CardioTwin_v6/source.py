"""Sample sources. The dashboard reads from any object with this interface:

    name        short text shown as connection status
    live        True for a real device, False for a replay
    advance(dt) move forward dt seconds
    window(sec) return (ecg_t, ecg_v, ppg_t, ppg_v) for the last `sec` seconds
    at_end      True when a replay has no more data

ReplaySource plays back a TI GUI export. LiveSource reads the AFE4950EVM over USB
through afe_serial.AFEStream, which follows TI's own capture scripts.
"""
import numpy as np

import pipeline as p


class ReplaySource:
    live = False

    def __init__(self, csv_path):
        self.name = f"Replay of recorded capture ({csv_path.name})"
        self.et, self.ev, self.pt, self.pv = p.load_capture(csv_path, trim=(p.TRIM_START_S, p.TRIM_END_S))
        self.t = 0.0
        self.duration = float(self.et[-1])

    @property
    def at_end(self):
        return self.t >= self.duration

    def advance(self, dt):
        self.t = min(self.t + dt, self.duration)

    def restart(self):
        self.t = 0.0

    def window(self, sec):
        lo = max(0.0, self.t - sec)
        me = (self.et >= lo) & (self.et <= self.t)
        mp = (self.pt >= lo) & (self.pt <= self.t)
        return self.et[me], self.ev[me], self.pt[mp], self.pv[mp]


class LiveSource:
    """Live AFE4950EVM over USB. Wraps an afe_serial.AFEStream running in a background thread."""
    live = True

    def __init__(self, stream):
        self.stream = stream

    @property
    def name(self):
        port = self.stream.port or "no port yet"
        return f"Live AFE4950EVM ({port}): {self.stream.state}"

    at_end = False

    def advance(self, dt):
        pass

    def restart(self):
        pass

    def window(self, sec):
        return self.stream.window(sec)

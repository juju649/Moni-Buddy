"""Send the board's finished results on to other devices.

Forwarder    posts one event every few seconds to the Moni-Buddy backend (FastAPI + Supabase).
             Events that cannot be sent wait in data/forward_queue.jsonl and go out later.
WatchBridge  shares the newest result with the ESP32 watch over Wi-Fi (HTTP) or over a USB serial cable.

Both read BoardLink.latest_summary(). Neither changes any measurement.
EXPERIMENTAL student prototype. Not a medical device. The risk number is a demo heuristic.
"""
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from edge_publisher import Queue, post_event

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SIM_ID = uuid.UUID("a1b2c3d4-0000-4000-8000-0000000000aa")  # fixed id, so the simulated alert upserts one row


class Forwarder:
    def __init__(self, link):
        self.link = link
        self.state = "idle"  # idle, running, error
        self.message = "Not started"
        self.sent = 0
        self.last_event = None
        self.last_http = None
        self.url = self.path = self.patient = None
        self.headers = {}
        self.every = 5.0
        self.queue = Queue(DATA / "forward_queue.jsonl")
        self._stop = threading.Event()
        self._thread = None
        self._last_seq = None
        self._last_t = 0.0

    @property
    def waiting(self):
        return len(self.queue.items)

    def start(self, url, path="/events", patient="p1", headers=None, every=5.0):
        self.stop()
        self.url, self.path, self.patient = url.strip(), path.strip() or "/events", patient.strip() or "p1"
        self.headers, self.every = headers or {}, float(every)
        self._stop.clear()
        self.state, self.message = "running", f"Sending to {self.url}{self.path}"
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None
        if self.state == "running":
            self.state, self.message = "idle", "Stopped"

    def _send(self, ev):
        self.last_http = post_event(self.url, self.path, ev, self.headers)
        return self.last_http

    def _run(self):
        while not self._stop.is_set():
            try:
                s = self.link.latest_summary()
                due = time.time() - self._last_t >= self.every
                if s and s["valid"] and s["seq"] != self._last_seq and due:
                    ev = {"event_id": str(uuid.uuid4()), "patient_id": self.patient, "state": s["state"],
                          "risk": s["risk"], "hr": s["hr"], "timestamp": s["time_utc"]}
                    self._last_seq, self._last_t = s["seq"], time.time()
                    self.queue.add(ev)
                    self.last_event = ev
                n = self.queue.flush(self._send, limit=10)
                self.sent += n
                if self.queue.items:
                    self.state, self.message = "error", f"Cannot reach {self.url}. {self.waiting} event(s) waiting. Will retry."
                else:
                    self.state, self.message = "running", f"Sending to {self.url}{self.path}. Last reply: HTTP {self.last_http}"
            except Exception as e:  # keep the thread alive
                self.state, self.message = "error", f"Forwarder error: {e}"
            self._stop.wait(1.0)

    def send_simulated_alert(self):
        """One clearly simulated high-risk event. It appears in the backend like any alert, so say so on stage."""
        if not self.url:
            raise RuntimeError("Press Start forwarding first so the backend address is set")
        ev = {"event_id": str(SIM_ID), "patient_id": self.patient, "state": "elevated_hr", "risk": 0.82, "hr": 128,
              "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        return self._send(ev)


class WatchBridge:
    """Share the newest result with the ESP32 watch.

    HTTP (Wi-Fi):  GET http://LAPTOP_IP:PORT/latest      JSON
                   GET http://LAPTOP_IP:PORT/latest.txt  one line:  hr,state,risk,quality_score,valid
    Serial (USB):  one text line per second:  CT,hr,state,risk,quality_score,valid
    """

    def __init__(self, link):
        self.link = link
        self.http_state = "off"
        self.serial_state = "off"
        self.requests = 0
        self.lines_written = 0
        self.http_port = None
        self._httpd = None
        self._stop = threading.Event()
        self._thread = None

    @staticmethod
    def as_line(s):
        if not s or not s["valid"]:
            return "0,waiting,0,0,0"
        return "%d,%s,%.2f,%.0f,1" % (s["hr"], s["state"], s["risk"], float(s["sqi"] or 0))

    # ---- HTTP
    def start_http(self, port=8080):
        self.stop_http()
        bridge = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                bridge.requests += 1
                s = bridge.link.latest_summary() or {"valid": False, "state": "waiting"}
                if self.path.startswith("/latest.txt"):
                    body, ctype = bridge.as_line(bridge.link.latest_summary()).encode(), "text/plain"
                elif self.path.startswith("/latest"):
                    body, ctype = json.dumps(s).encode(), "application/json"
                elif self.path == "/health":
                    body, ctype = b"ok", "text/plain"
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        try:
            self._httpd = ThreadingHTTPServer(("0.0.0.0", int(port)), H)
        except OSError as e:
            self.http_state = f"error: {e}"
            return
        self.http_port = int(port)
        self.http_state = "on"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def stop_http(self):
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        self.http_state = "off"

    # ---- serial
    def start_serial(self, port, baud=115200):
        self.stop_serial()
        try:
            import serial
            ser = serial.Serial(port, baud, timeout=0.5)
        except Exception as e:
            self.serial_state = f"error: {e}"
            return
        self.serial_state = f"on ({port})"
        self._stop.clear()

        def run():
            try:
                while not self._stop.is_set():
                    ser.write(("CT," + self.as_line(self.link.latest_summary()) + "\n").encode())
                    self.lines_written += 1
                    self._stop.wait(1.0)
            except Exception as e:
                self.serial_state = f"error: {e}"
            finally:
                try:
                    ser.close()
                except Exception:
                    pass

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def stop_serial(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None
        self.serial_state = "off"

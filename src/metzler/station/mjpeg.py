"""MJPEG relay for the outdoor station's camera (e.g. the Fritz!Box live picture).

The door only offers single JPEG snapshots (`/ISAPI/Streaming/channels/<n>/picture`,
HTTP Digest), no MJPEG. This relays them as a `multipart/x-mixed-replace` stream:

    GET /mjpeg         endless MJPEG stream
    GET /snapshot.jpg  newest single frame

The door is polled (throttled to `--mjpeg-fps`, default 4) only while a door
call is active AND somebody is watching; otherwise viewers get 503 and the
door is left alone. One poller serves all viewers.
"""

import base64
import hmac
import io
import logging
import re
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from metzler.station.door_unlock import digest_opener

log = logging.getLogger("indoor")

BOUNDARY = "mjpegframe"
JPEG_QUALITY = 60  # keeps the shrunk frame about as small as the door's own (~16 KB)


def parse_box(text):
    """'480x640' -> (480, 640); 'off'/''/'0' -> None"""
    m = re.fullmatch(r"\s*(\d+)\s*[x*]\s*(\d+)\s*", str(text or ""), re.IGNORECASE)
    return (int(m.group(1)), int(m.group(2))) if m and int(m.group(1)) and int(m.group(2)) else None


def shrink(jpeg, box):
    """JPEG bytes shrunk to fit `box` (aspect kept, never enlarged), or the
    input unchanged when it already fits. Pillow is imported lazily/optional."""
    from PIL import Image

    im = Image.open(io.BytesIO(jpeg))
    if im.width <= box[0] and im.height <= box[1]:
        return jpeg
    im.draft("RGB", box)  # decode straight at a reduced size when the JPEG allows it
    im.thumbnail(box, Image.Resampling.BILINEAR)
    out = io.BytesIO()
    im.convert("RGB").save(out, "JPEG", quality=JPEG_QUALITY)
    return out.getvalue()


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        """a client that resets, times out or sends non-HTTP junk is not an error"""
        log.debug("MJPEG: connection from %s dropped", client_address[0], exc_info=True)


class MjpegRelay:
    def __init__(self, o, door_host):
        """door_host() -> IP of the outdoor station while it has a call, else None"""
        self.o, self.door_host = o, door_host
        self.cond = threading.Condition()
        self.frame, self.seq = None, 0
        self.viewers, self.polling = 0, False
        self.httpd = None
        self.box = parse_box(getattr(o, "mjpeg_size", ""))
        self._resize_failed = False

    def start(self):
        relay = self

        class Handler(_Handler):
            pass

        Handler.relay = relay
        self.httpd = _Server((self.o.local_ip, self.o.mjpeg_port), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        log.info(
            "MJPEG relay on http://%s:%d/mjpeg (door polled at %d fps during door calls, frames %s)",
            self.o.local_ip,
            self.o.mjpeg_port,
            self.o.mjpeg_fps,
            f"fit {self.box[0]}x{self.box[1]}" if self.box else "unscaled",
        )

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()

    def acquire(self):
        with self.cond:
            self.viewers += 1
        self.ensure_poller()

    def release(self):
        with self.cond:
            self.viewers -= 1

    def ensure_poller(self):
        with self.cond:
            if self.polling:
                return
            self.polling = True
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def wait_frame(self, last_seq, timeout):
        """-> (seq, frame) newer than last_seq, or (last_seq, None) on timeout"""
        with self.cond:
            self.cond.wait_for(lambda: self.seq != last_seq and self.frame is not None, timeout)
            fresh = self.seq != last_seq and self.frame is not None
            return (self.seq, self.frame) if fresh else (last_seq, None)

    def _fetch(self, opener, host):
        url = f"http://{host}:{self.o.door_isapi_port}/ISAPI/Streaming/channels/{self.o.mjpeg_channel}/picture"
        try:
            with opener.open(url, timeout=5) as r:
                return r.read()
        except (urllib.error.URLError, OSError) as e:
            log.warning("MJPEG: snapshot from %s failed: %s", host, e)
            return None

    def _scaled(self, frame):
        """the door's frame shrunk to the configured size; the original when
        scaling is off or fails (e.g. Pillow missing) - warns once"""
        if not self.box:
            return frame
        try:
            return shrink(frame, self.box)
        except Exception as e:  # ImportError, a corrupt frame, ...
            if not self._resize_failed:
                self._resize_failed = True
                log.warning("MJPEG: cannot resize frames (%s: %s) - serving the door's size", type(e).__name__, e)
            return frame

    def _poll_loop(self):
        period, host, opener = 1.0 / self.o.mjpeg_fps, None, None
        log.info("MJPEG: polling the outdoor station")
        try:
            while self.viewers > 0:
                now_host = self.door_host()
                if not now_host:
                    break
                if now_host != host:
                    host = now_host
                    url = f"http://{host}:{self.o.door_isapi_port}/"
                    opener = digest_opener(url, self.o.door_isapi_user, self.o.door_isapi_pass or self.o.password)
                started = time.monotonic()
                frame = self._fetch(opener, host)
                if frame:
                    frame = self._scaled(frame)
                    with self.cond:
                        self.frame, self.seq = frame, self.seq + 1
                        self.cond.notify_all()
                time.sleep(max(0.0, period - (time.monotonic() - started)))
        finally:
            with self.cond:
                self.polling, self.frame = False, None
                self.cond.notify_all()
            log.info("MJPEG: polling stopped")


class _Handler(BaseHTTPRequestHandler):
    relay: MjpegRelay
    timeout = 10  # a viewer that vanished without FIN must not pin the poller

    def log_message(self, *a):
        pass

    def _reply(self, code, body=b"", ctype="text/plain", extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        pw = self.relay.o.mjpeg_pass
        if not pw:
            return True
        want = base64.b64encode(f"{self.relay.o.mjpeg_user}:{pw}".encode()).decode()
        got = (self.headers.get("Authorization") or "").removeprefix("Basic ")
        if hmac.compare_digest(got, want):
            return True
        self._reply(401, extra=[("WWW-Authenticate", 'Basic realm="door"')])
        return False

    def do_GET(self):
        path = self.path.split("?")[0]
        log.info("MJPEG request %s from %s (%s)", self.path, self.client_address[0], self.headers.get("User-Agent"))
        if path not in ("/mjpeg", "/snapshot.jpg"):
            return self._reply(404, b"not found")
        if not self._authorized():
            return
        if not self.relay.door_host():
            return self._reply(503, b"no active door call", extra=[("Retry-After", "5")])
        self.relay.acquire()
        try:
            self._snapshot() if path == "/snapshot.jpg" else self._stream()
        except OSError:  # viewer went away
            pass
        finally:
            self.relay.release()

    def _snapshot(self):
        _, frame = self.relay.wait_frame(-1, 5.0)
        if frame is None:
            return self._reply(504, b"no frame from the door")
        self._reply(200, frame, "image/jpeg", [("Cache-Control", "no-store")])

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        seq = -1
        while True:
            seq, frame = self.relay.wait_frame(seq, 1.5)
            if frame is None:
                if not self.relay.door_host():  # the call ended
                    return
                self.relay.ensure_poller()
                continue
            self.wfile.write(
                f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame)}\r\n\r\n".encode()
                + frame
                + b"\r\n"
            )

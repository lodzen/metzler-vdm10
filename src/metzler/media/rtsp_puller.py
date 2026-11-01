"""Pulling the caller's rtsp://<ip>:554 stream (the callee side of the symmetric pull)."""

import base64
import logging
import re
import socket
import threading

from metzler.media.rtsp import read_rtsp_request

log = logging.getLogger("indoor")


class RtspPuller(threading.Thread):
    """pulls the caller's rtsp://<caller>:554 stream -> their audio to the speaker,
    their video to ffplay. The callee side of the symmetric intercom pull."""
    def __init__(self, o, host, on_audio, on_video):
        super().__init__(daemon=True)
        self.o, self.host = o, host
        self.on_audio, self.on_video = on_audio, on_video
        self.running = True
        self.conn = None
        self.seen = set()

    def run(self):
        self.uri = f"rtsp://{self.host}:{self.o.rtsp_port}/ch1/main/av_stream"
        self.tok = base64.b64encode(f"admin:{self.o.password}".encode()).decode()
        self.buf, self.session, self.cseq = b"", "", 0
        try:
            self.conn = socket.create_connection((self.host, self.o.rtsp_port), timeout=5)
        except OSError as e:
            log.warning("cannot reach caller's RTSP %s: %s", self.uri, e); return
        try:
            self._negotiate()
            self._receive()
        except OSError as e:
            log.warning("RTSP pull of %s failed: %s", self.uri, e)
        try: self.conn.close()
        except OSError: pass
        log.info("RTSP pull of %s ended", self.uri)

    def _request(self, method, url, extra=""):
        self.cseq += 1
        self.conn.sendall((f"{method} {url} RTSP/1.0\r\nCSeq: {self.cseq}\r\n"
                           f"Authorization: Basic {self.tok}\r\n"
                           + (f"Session: {self.session}\r\n" if self.session else "")
                           + extra + "\r\n").encode())
        r, self.buf = read_rtsp_request(self.conn, self.buf)
        r = r.decode(errors="replace") if r else ""
        m = re.search(r"Session:\s*([^;\s]+)", r)
        if m: self.session = m.group(1)     # the session id is issued by SETUP
        return r

    def _negotiate(self):
        """OPTIONS -> DESCRIBE -> SETUP per track (interleaved TCP) -> PLAY"""
        self._request("OPTIONS", self.uri)
        r = self._request("DESCRIBE", self.uri, "Accept: application/sdp\r\n")
        # figure out which control names the server uses (trackID=1/2 or 0/1)
        tracks = re.findall(r"a=control:(\S+)", r) or ["trackID=1", "trackID=2"]
        tracks = [t for t in tracks if t != "*"][:2]
        for i, t in enumerate(tracks):
            self._request("SETUP", f"{self.uri}/{t}",
                          f"Transport: RTP/AVP/TCP;unicast;interleaved={i*2}-{i*2+1}\r\n")
        r = self._request("PLAY", self.uri, "Range: npt=0.000-\r\n")
        log.info("pulling caller's stream %s (session %s): %s",
                 self.uri, self.session or "?", r.split("\r\n")[0] if r else "no reply")

    def _receive(self):
        self.conn.settimeout(5)
        while self.running:
            try: d = self.conn.recv(65535)
            except TimeoutError: continue
            except OSError: break
            if not d: break
            self.buf += d
            self._drain_frames()

    def _drain_frames(self):
        """split $-interleaved frames: {'$', channel, u16 length, RTP}"""
        while len(self.buf) >= 4 and self.buf[:1] == b"$":
            ch, ln = self.buf[1], int.from_bytes(self.buf[2:4], "big")
            if len(self.buf) < 4 + ln: break
            frame, self.buf = self.buf[4:4 + ln], self.buf[4 + ln:]
            if len(frame) >= 13:
                self._on_frame(ch, frame, ln)

    def _on_frame(self, ch, frame, ln):
        pt, payload = frame[1] & 0x7F, frame[12:]
        if ch not in self.seen:
            self.seen.add(ch)
            log.info("caller stream: first frame on channel %d, PT %d, %dB", ch, pt, ln)
        if ch < 2 and self.on_video:
            self.on_video(payload, pt)
        elif pt == 0 and self.on_audio:
            self.on_audio(payload)

    def stop(self):
        self.running = False
        try:
            if self.conn:
                self.conn.sendall(b"TEARDOWN rtsp://x RTSP/1.0\r\nCSeq: 99\r\n\r\n")
                self.conn.close()
        except OSError: pass

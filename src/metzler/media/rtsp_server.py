"""Our :554 - the RTSP server the calling station pulls our audio from."""

import logging
import random
import re
import socket
import struct
import subprocess
import threading
import time

from metzler.media.rtsp import RTSP_SDP_AV, read_rtsp_request, rtsp_header, rtsp_reply

log = logging.getLogger("indoor")


def _spawn_test_video():
    """ffmpeg: black 320x240 H264 test stream on stdout (None without ffmpeg)"""
    try:
        return subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-re",
             "-f", "lavfi", "-i", "color=c=black:s=320x240:r=12.5",
             "-c:v", "libx264", "-profile:v", "main", "-level", "2.0",
             "-x264-params", "keyint=25", "-f", "h264", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return None


def _annexb_nals(data):
    """split an annex-B byte stream at its 00 00 01 / 00 00 00 01 start codes"""
    for part in re.split(b"\x00\x00\x01|\x00\x00\x00\x01", data):
        if part: yield part


class RtspServer(threading.Thread):
    """hikvision-style RTSP server on :554 - the caller pulls our stream here.
    DESCRIBE always advertises video+audio (mandatory for their client); the
    data path sends only 320B/40ms PCMU, exactly like a real indoor unit."""
    def __init__(self, o):
        super().__init__(daemon=True)
        self.o = o
        self.sdp = RTSP_SDP_AV
        self.mic = None                 # callable -> 160B ulaw, set while a call runs
        self.ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.ls.bind((o.local_ip, o.rtsp_port)); self.ls.listen(4)
        self.running = True

    def run(self):
        while self.running:
            try: conn, addr = self.ls.accept()
            except OSError: return
            threading.Thread(target=self.handle, args=(conn, addr), daemon=True).start()

    def handle(self, conn, addr):
        log.info("RTSP client connected: %s:%d", *addr)
        conn.settimeout(30)
        sess = _RtspSession(self, conn)
        buf = b""
        try:
            while True:
                req, buf = read_rtsp_request(conn, buf)
                if req is None or not sess.dispatch(req, addr): break
        except (OSError, TimeoutError):
            pass
        sess.playing[0] = False
        try: conn.close()
        except OSError: pass
        log.info("RTSP client %s:%d disconnected", *addr)

    def _send(self, conn, wlock, chan, rtp):
        f = b"$" + bytes([chan]) + len(rtp).to_bytes(2, "big") + rtp
        with wlock:
            conn.sendall(f)

    def _nal_rtp(self, nal, seq, ts, ssrc):
        """packetize one annex-B NAL into PT96 RTP packet(s) (FU-A when needed)"""
        out = []
        hdr = lambda m, s, t: struct.pack(">BBHII", 0x80, 96 | (m << 7), s, t, ssrc)
        if len(nal) <= 1300:
            out.append(hdr(1, seq[0], ts) + nal); seq[0] += 1
        else:
            ind, nal_type = nal[0] & 0xE0 | 28, nal[0] & 0x1F
            body = nal[1:]
            while body:
                part, body = body[:1300], body[1300:]
                fu = nal_type | (0x80 if not out else 0) | (0x40 if not body else 0)
                fuh = (ind << 8) | fu
                out.append(hdr(0 if body else 1, seq[0], ts) + fuh.to_bytes(2, "big") + part)
                seq[0] += 1
        return out

    def stream(self, conn, wlock, playing, chmap, ssrcs):
        """send the stream exactly like a real indoor unit serves it:
        the SDP advertises video+audio (their client requires the video track),
        but on the wire the unit emits ONLY PCMU audio - 320B payloads, 40 ms,
        ts += 320 @8kHz - never a single video RTP packet (verified from a
        live pull of the main indoor's :554 during an extension call:
        1.5 MB, all ch2 PT0 320B, zero ch0). The RTP ssrc must equal the one
        we declared in the SETUP reply or their player drops the packets."""
        seq_a = [random.getrandbits(15)]
        ts_a = [random.getrandbits(32)]
        ssrc = ssrcs.get("audio", random.getrandbits(32))
        ch_a = chmap.get("audio")
        ch_v = chmap.get("video")
        if getattr(self.o, "rtsp_video", False) and ch_v is not None:
            threading.Thread(target=self._video_feed, args=(conn, wlock, playing, ch_v,
                                                            ssrcs.get("video",
                                                                      random.getrandbits(32))),
                             daemon=True).start()
        next_a = 0.0
        while playing[0]:
            try:
                if self.mic and ch_a is not None:
                    if time.time() < next_a:
                        time.sleep(min(next_a - time.time(), 0.02))
                        continue
                    payload = self.mic() + self.mic()      # 2 x 160B = 40 ms
                    rtp = struct.pack(">BBHII", 0x80, 0, seq_a[0] & 0xFFFF,
                                      ts_a[0] & 0xFFFFFFFF, ssrc) + payload
                    self._send(conn, wlock, ch_a, rtp)
                    seq_a[0] += 1; ts_a[0] += 320; next_a = time.time() + 0.04
                else:
                    time.sleep(0.01)
            except OSError:
                break

    def _video_feed(self, conn, wlock, playing, ch_v, ssrc):
        """optional (--rtsp-video): ffmpeg-generated H264 like a door/camera unit
        would send. Main profile @2.0 320x240, annex-B, packetized via _nal_rtp."""
        proc = _spawn_test_video()
        if not proc: return
        seq = [random.getrandbits(15)]; ts = [random.getrandbits(32)]
        buf = b""
        try:
            while playing[0]:
                chunk = proc.stdout.read(4096)
                if not chunk: break
                buf += chunk
                i = buf.rfind(b"\x00\x00\x01")
                if i < 4: continue
                ready, buf = buf[:i], buf[i:]
                for nal in _annexb_nals(ready):
                    for rtp in self._nal_rtp(nal, seq, ts[0], ssrc):
                        self._send(conn, wlock, ch_v, rtp)
                    ts[0] += 3600          # 40 ms @90kHz
        except OSError:
            pass
        finally:
            proc.terminate()


class _RtspSession:
    """state of one client connection to RtspServer: one method per RTSP verb"""
    def __init__(self, server, conn):
        self.server, self.conn = server, conn
        self.id = str(random.getrandbits(31))
        self.playing = [False]
        self.chmap = {}                 # 'audio'/'video' -> interleaved base channel
        self.ssrcs = {}                 # 'audio'/'video' -> ssrc declared in SETUP reply
        self.ssrc_base = random.getrandbits(32)
        self.wlock = threading.Lock()

    def dispatch(self, req, addr):
        """handle one request; False once the client tore the session down"""
        try:
            lines = req.decode(errors="replace").split("\r\n")
            method, url = lines[0].split()[:2]
        except (ValueError, IndexError, AttributeError) as e:
            log.debug("unparseable RTSP request from %s: %s", addr, e)
            return True
        handler = getattr(self, "on_" + method.lower(), None)
        if handler:
            return handler(rtsp_header(lines, "cseq") or "0", url, lines) is not False
        return True

    def _reply(self, cseq, extra="", body=None):
        rtsp_reply(self.conn, cseq, extra=f"Session: {self.id}\r\n{extra}", body=body)

    def on_options(self, cseq, url, lines):
        rtsp_reply(self.conn, cseq, extra="Public: OPTIONS, DESCRIBE, SETUP, "
                                           "TEARDOWN, PLAY, PAUSE\r\n")

    def on_describe(self, cseq, url, lines):
        self._reply(cseq, body=self.server.sdp)

    def on_setup(self, cseq, url, lines):
        il = re.search(r"interleaved=(\d+)-(\d+)", rtsp_header(lines, "transport"))
        ch = il.group(0).split("=", 1)[1] if il else "0-1"
        track = ("video" if "trackID=1" in url else
                 "audio" if "trackID=2" in url else
                 "audio" if "video" not in self.chmap else "video")
        self.chmap[track] = int(ch.split("-")[0])
        self.ssrcs.setdefault(track, (self.ssrc_base + (0 if track == "video" else 1)) & 0xFFFFFFFF)
        self._reply(cseq, f"Transport: RTP/AVP/TCP;unicast;interleaved={ch}"
                          f";ssrc={self.ssrcs[track]:08x}\r\n")

    def on_play(self, cseq, url, lines):
        self._reply(cseq)
        if not self.playing[0]:
            self.playing[0] = True
            threading.Thread(target=self.server.stream,
                             args=(self.conn, self.wlock, self.playing, self.chmap, self.ssrcs),
                             daemon=True).start()

    def on_pause(self, cseq, url, lines):
        self.playing[0] = False
        self._reply(cseq)

    def on_teardown(self, cseq, url, lines):
        self.on_pause(cseq, url, lines)
        return False

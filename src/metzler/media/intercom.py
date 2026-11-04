"""Media adapters for intercom (RTSP) calls: to the local headset, or to a SIP peer."""

import collections
import logging
import random
import shutil
import socket
import struct
import subprocess
import threading
import time

from metzler.media.g711 import pcm_to_ulaw, ulaw_to_pcm
from metzler.media.rtp import bind_rtp, is_rtp
from metzler.media.rtsp_puller import RtspPuller

log = logging.getLogger("indoor")


class IntercomMedia:
    """local-mode media for intercom calls: serves our mic over the shared RtspServer
    and pulls the caller's stream. Owns aplay/arecord/ffplay like LocalMediaBridge."""

    def __init__(self, o, server, caller_ip):
        self.o, self.server, self.caller_ip = o, server, caller_ip
        self.aplay = self.arecord = self.ffplay = None
        self.puller = None
        self.door_a = self.door_v = None  # for log compatibility
        self.rx = {True: 0, False: 0}
        self.tx = 0
        self._lock = threading.Lock()
        self._next = 0.0

    def _spawn(self, cmd):
        try:
            return subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE if cmd[0] == "aplay" else None,
                stdout=subprocess.PIPE if cmd[0] != "aplay" else None,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            log.warning("%s not found - disabled", cmd[0])
            return None

    def start(self):
        o = self.o
        if o.local_audio:
            self.aplay = self._spawn(["aplay", "-q", "-D", o.alsa_device, "-f", "S16_LE", "-r", "8000", "-c", "1"])
        if o.local_mic:
            self.arecord = self._spawn(["arecord", "-q", "-D", o.alsa_device, "-f", "S16_LE", "-r", "8000", "-c", "1"])
        if o.video_player and shutil.which("ffplay"):
            self.ffplay = subprocess.Popen(
                ["ffplay", "-f", "h264", "-fflags", "nobuffer", "-probesize", "32", "-"],
                stdin=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        self.server.mic = self.mic_frame
        self.puller = RtspPuller(o, self.caller_ip, self.on_audio, self.on_video)
        self.puller.start()

    def mic_frame(self):
        """called by RtspServer.stream - one 20ms ulaw frame"""
        if self.arecord and self.arecord.stdout:
            try:
                pcm = self.arecord.stdout.read(320)
                if pcm and len(pcm) == 320:
                    self.tx += 1
                    return pcm_to_ulaw(pcm)
            except (OSError, ValueError):
                pass
        return b"\xff" * 160

    def on_audio(self, ulaw):
        self.rx[True] += 1
        if self.aplay and self.aplay.stdin:
            try:
                self.aplay.stdin.write(ulaw_to_pcm(ulaw))
            except (BrokenPipeError, OSError, ValueError):
                pass

    def on_video(self, frame):
        self.rx[False] += 1
        if self.ffplay and self.ffplay.stdin:
            try:
                self.ffplay.stdin.write(frame[12:])
            except (BrokenPipeError, OSError, ValueError):
                pass

    def stop(self):
        log.info(
            "intercom media stopped: RTSP rx audio=%d video=%d, tx audio=%d", self.rx[True], self.rx[False], self.tx
        )
        if self.puller:
            self.puller.stop()
        if self.server and self.server.mic == self.mic_frame:
            self.server.mic = None
        for p in (self.aplay, self.arecord, self.ffplay):
            if not p:
                continue
            for f in (p.stdin, p.stdout, p.stderr):
                try:
                    f and f.close()
                except (OSError, ValueError):
                    pass
            p.terminate()
            try:
                p.wait(timeout=1)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()


class IntercomVideoLeg:
    """provider-facing video channel for door calls: repacks each pulled
    H264 RTP frame as PT96 with our seq/ssrc (source timestamp kept) and
    protects it when the provider leg is SRTP. offer_sdp/_media_block use
    the .rtp_port/.srtp attributes like any other media object."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        bind_rtp(self.sock)
        self.rtp_port = self.sock.getsockname()[1]
        self.peer = None  # provider video addr from answer SDP
        self.srtp = None  # SrtpSession bound via bind_srtp
        self.seq = random.getrandbits(15)
        self.ssrc = random.getrandbits(32)
        self.tx = 0

    def forward(self, frame):
        """door's pulled RTP frame -> provider leg (PT96, our seq/ssrc)"""
        if not self.peer or len(frame) < 12:
            return
        pkt = (
            bytes([frame[0], (frame[1] & 0x80) | 96])
            + struct.pack(">H", self.seq)
            + frame[4:8]
            + struct.pack(">I", self.ssrc)
            + frame[12:]
        )
        self.seq = (self.seq + 1) & 0xFFFF
        if self.srtp:
            pkt = self.srtp.tx.protect(pkt)
        try:
            self.sock.sendto(pkt, self.peer)
            self.tx += 1
        except OSError:
            pass

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class SipIntercomMedia:
    """provider-mode media for intercom calls (header-only SDP): pulls the
    caller's rtsp://<caller>:554 PCMU and forwards it as RTP (PT0) to the SIP
    peer; incoming SIP peer RTP feeds RtspServer.mic so the caller's pull
    hears the peer. Replaces the ALSA headset with a UDP RTP socket.
    With video=True (door calls only) also forwards the pulled H264 stream
    as a PT96 RTP leg to the provider peer."""

    def __init__(self, o, server, caller_ip, video=False):
        self.o, self.server, self.caller_ip = o, server, caller_ip
        self.video = IntercomVideoLeg() if video else None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        bind_rtp(self.sock)
        self.rtp_port = self.sock.getsockname()[1]
        self.peer = None  # provider RTP (ip, port)
        self.srtp = None  # SrtpSession when the leg is encrypted
        self.rxq = collections.deque()  # peer PCMU payloads -> caller pull
        self.puller = None
        self.running = True
        self.seq = random.getrandbits(15)
        self.ts = random.getrandbits(32)
        self.ssrc = random.getrandbits(32)
        self.rx = {True: 0, False: 0}
        self.tx = 0
        self.door_a = self.door_v = None  # for log compatibility
        self.on_dtmf = None  # set by the station: digit sink
        self.dtmf_pt = None  # telephone-event PT from the answer
        self._dropped = 0  # diagnostic: non-PT0 packets logged
        self._srtp_ok = False  # first authenticated peer packet seen
        self._plain = 0  # pre-auth plain-RTP packets accepted
        self._last_dtmf = -1
        self._last_dtmf_ts = 0.0

    def start(self):
        self.server.mic = self.mic_frame
        if self.puller:  # re-entry (a racing leg won after early
            return  # media already started): peers re-latched
        self.puller = RtspPuller(self.o, self.caller_ip, self.on_audio, self.on_video)
        self.puller.start()
        threading.Thread(target=self._recv_loop, daemon=True).start()

    def set_provider(self, addr):
        self.peer = addr

    def on_audio(self, ulaw):
        """caller's PCMU frame (320B/40ms) -> provider RTP"""
        self.rx[True] += 1
        if not self.peer:
            return
        rtp = struct.pack(">BBHII", 0x80, 0, self.seq & 0xFFFF, self.ts & 0xFFFFFFFF, self.ssrc) + ulaw
        if self.srtp:
            rtp = self.srtp.tx.protect(rtp)
        try:
            self.sock.sendto(rtp, self.peer)
            self.seq = (self.seq + 1) & 0xFFFF
            self.ts = (self.ts + len(ulaw)) & 0xFFFFFFFF
        except OSError:
            pass

    def on_video(self, frame):
        self.rx[False] += 1
        if self.video:  # door call: forward the door's H264
            self.video.forward(frame)

    def _recv_loop(self):
        self.sock.settimeout(0.5)
        while self.running:
            try:
                data, addr = self.sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                return
            if self.peer is None:
                self.peer = addr  # latch (symmetric RTP)
            payload = self._pcmu_payload(data)
            if payload is None:
                continue
            self.rxq.append(payload)
            while len(self.rxq) > 50:  # ~1s jitter bound
                self.rxq.popleft()

    def _pcmu_payload(self, data):
        """peer packet -> PCMU payload; PT101 telephone-event digits are
        tapped into on_dtmf first. None for RTCP, a bad SRTP tag (after the
        first authenticated packet), or any PT other than PT0 (the only codec
        we offer). Pre-key early media arrives unencrypted and is accepted as
        plain RTP until the first SRTP packet authenticates."""
        if not is_rtp(data):
            return None
        raw = data
        data = self._unwrap(data)
        if data is None or len(data) < 13:
            if self._dropped < 20:
                self._dropped += 1
                log.info("peer RTP dropped: bad SRTP/short, %dB, PT %d", len(raw), raw[1] & 0x7F)
            return None
        pt = data[1] & 0x7F
        if self.dtmf_pt is not None and pt == self.dtmf_pt:
            d = self._dtmf_event(data)
            if d and self.on_dtmf:
                self.on_dtmf(d)
            return None
        if pt != 0:
            if self._dropped < 20:
                self._dropped += 1
                log.info(
                    "peer RTP dropped: PT %d, %dB, payload %s (dtmf_pt=%s)",
                    pt,
                    len(data),
                    data[12:20].hex(),
                    self.dtmf_pt,
                )
            return None
        return data[12:]

    def _unwrap(self, data):
        """SRTP-unprotect. Early media sent before the peer's SRTP engine
        armed arrives unencrypted: pass it through until the first packet
        authenticates, then strict (None on auth failure)"""
        if not (self.srtp and self.srtp.rx):
            return data
        out = self.srtp.rx.unprotect(data)
        if out is not None:
            self._srtp_ok = True
            return out
        if self._srtp_ok:
            return None
        self._plain += 1
        log.debug("peer RTP: plain %dB PT %d accepted pre-SRTP-auth", len(data), data[1] & 0x7F)
        if self._plain == 50:
            log.info("peer keeps sending unencrypted RTP on an SRTP-negotiated leg")
        return data

    DTMF_CHARS = "0123456789*#ABCD"

    def _dtmf_event(self, pkt):
        """RFC4733 telephone-event: emit the digit once on the end-bit
        packet; the end packet's own retransmits (~400ms) are suppressed"""
        ev, flags = pkt[12], pkt[13]
        if not flags & 0x80 or ev >= len(self.DTMF_CHARS):  # E bit = top bit of byte 1
            return None
        now = time.monotonic()
        if ev == self._last_dtmf and now - self._last_dtmf_ts < 0.4:
            return None
        self._last_dtmf, self._last_dtmf_ts = ev, now
        return self.DTMF_CHARS[ev]

    def mic_frame(self):
        """called by RtspServer.stream - one 160B/20ms ulaw frame of PEER audio"""
        if not self.rxq:
            return b"\xff" * 160
        p = self.rxq.popleft()
        self.tx += 1
        if len(p) <= 160:
            return p.ljust(160, b"\xff")
        self.rxq.appendleft(p[160:])
        return p[:160]

    def stop(self):
        log.info(
            "sip intercom media stopped: RTSP rx audio=%d video=%d, tx audio=%d video=%d",
            self.rx[True],
            self.rx[False],
            self.tx,
            self.video.tx if self.video else 0,
        )
        if self.video:
            self.video.close()
        self.running = False
        if self.puller:
            self.puller.stop()
        if self.server and self.server.mic == self.mic_frame:
            self.server.mic = None
        try:
            self.sock.close()
        except OSError:
            pass

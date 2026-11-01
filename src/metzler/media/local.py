"""Local headset mode for door calls: RTP <-> aplay/arecord (+ optional ffplay video)."""

import logging
import math
import random
import select
import shutil
import socket
import struct
import subprocess
import threading
import time

from metzler.media.g711 import alaw_to_pcm, pcm_to_ulaw, ulaw_encode_sample, ulaw_to_pcm
from metzler.media.rtp import bind_rtp

log = logging.getLogger("indoor")


def _feed(proc, data):
    """write to a player's stdin; a player that went away is not an error"""
    if proc and proc.stdin:
        try: proc.stdin.write(data)
        except (BrokenPipeError, OSError, ValueError): pass


class LocalMediaBridge(threading.Thread):
    """Door RTP <-> local speaker/mic via aplay/arecord; optional video pipe to ffplay."""
    def __init__(self, o, door_a, door_v, fixed=False):
        """fixed=True: hicore 'talkback' mode - audio RTP on the fixed port (9654) and RTCP on
        port+1, video (PT 96) arrives on the same socket; used when the offer has no m= lines"""
        super().__init__(daemon=True)
        self.o = o
        self.fixed = fixed
        self.audio_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.video_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtcp_sock = None
        if fixed:
            self.audio_sock.bind((o.local_ip, o.talkback_port))
            try:
                self.rtcp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.rtcp_sock.bind((o.local_ip, o.talkback_port + 1))
            except OSError: self.rtcp_sock = None
            bind_rtp(self.video_sock)
        else:
            bind_rtp(self.audio_sock); bind_rtp(self.video_sock)
        self.audio_port = self.audio_sock.getsockname()[1]
        self.video_port = self.video_sock.getsockname()[1]
        self.pt_seen = set()
        self.door_a = door_a            # (ip, port) from door SDP
        self.door_v = door_v
        self.running = True
        self.aplay = self.arecord = self.ffplay = None
        self.rx = {True: 0, False: 0}   # packets received: audio / video
        self.tx = 0                     # audio packets sent
        self.seq = random.getrandbits(16); self.ts = random.getrandbits(32)
        self.ssrc = random.getrandbits(32)

    def _spawn(self, cmd):
        try:
            return subprocess.Popen(cmd, stdin=subprocess.PIPE if cmd[0] == "aplay" else None,
                                    stdout=subprocess.PIPE if cmd[0] != "aplay" else None,
                                    stderr=subprocess.PIPE)
        except FileNotFoundError:
            log.warning("%s not found — %s disabled", cmd[0], "playback" if cmd[0] == "aplay" else "capture")
            return None

    def start(self):
        o = self.o
        dev = o.alsa_device
        if o.local_audio:
            self.aplay = self._spawn(["aplay", "-q", "-D", dev, "-f", "S16_LE", "-r", "8000", "-c", "1"])
        if o.local_mic:
            self.arecord = self._spawn(["arecord", "-q", "-D", dev, "-f", "S16_LE", "-r", "8000", "-c", "1"])
        if o.video_player and shutil.which("ffplay"):
            self.ffplay = subprocess.Popen(["ffplay", "-f", "h264", "-fflags", "nobuffer", "-probesize", "32", "-"],
                                           stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        threading.Thread(target=self.rx_loop, daemon=True).start()
        threading.Thread(target=self.tx_loop, daemon=True).start()
        time.sleep(0.4)
        for name, p in (("aplay (speaker)", self.aplay), ("arecord (microphone)", self.arecord)):
            if p and p.poll() is not None:
                log.warning("%s exited at once (code %s): %s", name, p.returncode,
                            (p.stderr.read() or b"").decode(errors="replace").strip() or "no error text")

    def rx_loop(self):
        """door -> speaker (+ video -> ffplay)"""
        while self.running:
            socks = [self.audio_sock, self.video_sock] + ([self.rtcp_sock] if self.rtcp_sock else [])
            try: r, _, _ = select.select(socks, [], [], 0.5)
            except (OSError, ValueError): return
            for s in r:
                try: data, addr = s.recvfrom(65535)
                except OSError: return
                if s is not self.rtcp_sock and len(data) >= 13:
                    self.on_packet(s, data, addr)

    def on_packet(self, sock, data, addr):
        """one RTP packet from the door: classify audio/video, then play it"""
        pt = data[1] & 0x7f
        # fixed (talkback) mode: audio and video share the socket, tell them apart by PT
        is_audio = pt in (0, 2, 8, 9, 98) if self.fixed else sock is self.audio_sock
        self.rx[is_audio] += 1
        if pt not in self.pt_seen:
            self.pt_seen.add(pt)
            log.info("first RTP packet with payload type %d from %s:%d (%d bytes) on the %s socket",
                     pt, addr[0], addr[1], len(data), "audio" if sock is self.audio_sock else "video")
        if is_audio:
            self._play_audio(pt, data[12:], addr)
        else:
            self._play_video(data[12:], addr)

    def _play_audio(self, pt, payload, addr):
        if self.door_a is None or (self.fixed and self.rx[True] == 1 and addr != self.door_a):
            if self.door_a: log.info("RTP peer is %s:%d (not %s:%d), following it", *addr, *self.door_a)
            self.door_a = addr
        decode = {0: ulaw_to_pcm, 8: alaw_to_pcm}.get(pt)
        if decode is None:
            if self.rx[True] == 1: log.warning("audio payload type %d is not decoded (only PCMU/PCMA)", pt)
            return
        _feed(self.aplay, decode(payload))

    def _play_video(self, payload, addr):
        if self.door_v is None and not self.fixed: self.door_v = addr
        _feed(self.ffplay, payload)

    def _mic_frame(self):
        if self.arecord and self.arecord.stdout:
            try:
                pcm = self.arecord.stdout.read(320)   # 160 samples = 20 ms
                if pcm: return pcm_to_ulaw(pcm)
            except (OSError, ValueError):
                pass
        if self.o.local_tone:
            f = self.o.local_tone
            return bytes(ulaw_encode_sample(int(8000 * math.sin(2 * math.pi * f * (self.ts + n) / 8000)))
                         for n in range(160))
        return b"\xff" * 160                        # ulaw silence

    def tx_loop(self):
        """mic/tone -> door"""
        while self.running:
            if not self.door_a:
                time.sleep(0.05); continue
            payload = self._mic_frame()
            hdr = struct.pack(">BBHII", 0x80, 0, self.seq & 0xFFFF,
                              self.ts & 0xFFFFFFFF, self.ssrc)
            try:
                self.audio_sock.sendto(hdr + payload, self.door_a)
                self.tx += 1
                if self.tx == 1: log.info("first RTP audio packet sent to %s:%d", *self.door_a)
            except OSError: pass
            self.seq += 1; self.ts += 160
            time.sleep(0.02)

    def stop(self):
        self.running = False
        log.info("media stopped: RTP rx audio=%d video=%d, tx audio=%d (peer %s)",
                 self.rx[True], self.rx[False], self.tx, self.door_a)
        for p in (self.aplay, self.arecord, self.ffplay):
            if not p: continue
            # aplay/arecord restart blocked pipe I/O after SIGTERM: close the pipes first
            # or they linger and keep the headset busy
            for f in (p.stdin, p.stdout, p.stderr):
                try: f and f.close()
                except (OSError, ValueError): pass
            p.terminate()
            try: p.wait(timeout=1)
            except subprocess.TimeoutExpired:
                p.kill(); p.wait()
        for s in (self.audio_sock, self.video_sock):
            try: s.close()
            except OSError: pass

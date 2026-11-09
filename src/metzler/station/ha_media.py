"""Embedded/HA mode media (vdm10-ha): queue-fed mic, no ALSA/ffplay/SIP peer.

Same two media shapes as LocalAnswerMixin._local_media:
- intercom calls (header-only offer): the caller RTSP-pulls our :554 for mic
  audio; HaIntercomMedia feeds RtspServer.mic from an injected frame source.
  No RtspPuller - nothing here consumes the caller's stream (the HA camera
  pulls the door's RTSP itself, on demand only).
- door calls (m= lines): HaCallMedia is a LocalMediaBridge without ALSA -
  door RTP audio is (optionally) tapped into a callback, and our talkback
  RTP to the door is fed from the injected frame source (PCMU silence while
  the queue is empty -> muted by default).
"""

import logging
import socket
import threading

from metzler.media.local import LocalMediaBridge

log = logging.getLogger("indoor")

SILENCE = b"\xff" * 160  # one 20 ms PCMU frame of silence


class HaIntercomMedia:
    """intercom (header-only) calls: feed the shared RtspServer's mic from an
    injected frame source. Door/intercom audio+video is not consumed here."""

    def __init__(self, server, mic_feed):
        self.server, self.mic_feed = server, mic_feed
        # attributes LocalMediaBridge exposes, kept for Call.stop()/log compat
        self.door_a = self.door_v = None
        self.rx = {True: 0, False: 0}
        self.tx = 0

    def start(self):
        self.server.mic = self.mic_frame

    def mic_frame(self):
        """called by RtspServer.stream - one 160B/20ms ulaw frame"""
        frame = self.mic_feed() if self.mic_feed else None
        if frame is None:
            return SILENCE
        self.tx += 1
        return frame

    def stop(self):
        if self.server and self.server.mic == self.mic_frame:
            self.server.mic = None


class HaCallMedia(LocalMediaBridge):
    """door calls (negotiated m= lines): LocalMediaBridge without ALSA. Mic
    frames come from an injected callable instead of arecord; door audio is
    tapped into an optional callback instead of aplay; video frames are dropped.
    Socket setup, the 20 ms RTP tx loop, and peer latching are the parent's."""

    def __init__(self, o, door_a, door_v, fixed=False, mic_feed=None, on_audio=None):
        super().__init__(o, door_a, door_v, fixed=fixed)
        self.mic_feed = mic_feed
        self.on_audio_cb = on_audio

    def start(self):
        """same sockets as the parent, but never spawns aplay/arecord/ffplay"""
        threading.Thread(target=self.rx_loop, daemon=True).start()
        threading.Thread(target=self.tx_loop, daemon=True).start()

    def _mic_frame(self):
        """one 160B PCMU frame from the HA mic queue; silence while muted"""
        frame = self.mic_feed() if self.mic_feed else None
        return frame if frame is not None else SILENCE

    def _play_audio(self, pt, payload, addr):
        """keep the parent's peer latching; tap PCMU into the callback"""
        if self.door_a is None or (self.fixed and self.rx[True] == 1 and addr != self.door_a):
            self.door_a = addr
        if self.on_audio_cb is not None:
            self.on_audio_cb(payload)

    def _play_video(self, payload, addr):
        if self.door_v is None and not self.fixed:
            self.door_v = addr  # keep peer latching; frames are dropped

    def stop(self):
        # shutdown() before close: close() alone does not wake rx_loop's recvfrom
        for sock in (self.audio_sock, self.video_sock):
            try:
                sock and sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        super().stop()
        try:
            self.rtcp_sock and self.rtcp_sock.close()
        except OSError:
            pass

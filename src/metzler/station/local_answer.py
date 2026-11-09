"""Local headset mode: answer calls on this machine."""

import logging
import time

from metzler.media.intercom import IntercomMedia
from metzler.media.local import LocalMediaBridge
from metzler.sip.sdp import sdp_answer_firmware, sdp_answer_talkback

log = logging.getLogger("indoor")


class LocalAnswerMixin:
    """Local headset mode: answer calls on this machine."""

    def answer_locally(self, call, door_a, door_v, remote_sdp):
        """--local-audio mode: answer the door call ourselves, media <-> headset.
        Runs off the listener thread; a CANCEL/BYE during the ring delay aborts the answer
        (answering a cancelled call leaves stale dialog state on the registrar).
        Ring-only unless --auto-answer: the main indoor treats an answering extension as
        'call taken' and stops ringing the real stations. Policy 'extension' answers only
        calls the main indoor places to us (Subject 'This is a call to extension.'), never
        the doorbell legs ('This is a call to app.')."""
        if not self._should_answer(call):
            return
        if self.o.answer_delay:
            time.sleep(self.o.answer_delay)
        if call.state != "early" or not self.running:
            return
        talkback = not remote_sdp["media"]  # hicore intercom offers carry no m= lines
        # Intercom calls don't use talkback RTP: the caller RTSP-pulls our :554
        # (our mic) and we pull the caller's :554 (their mic) - see IntercomMedia.
        # The 200 OK still carries m=audio 9654: the caller's ACK path only needs
        # some m= line to accept the answer before it opens the RTSP pull.
        intercom = talkback and self.rtsp
        try:
            call.local = self._local_media(call, door_a, door_v, remote_sdp, talkback, intercom)
        except OSError as e:
            # e.g. a prior call's bridge still holds the fixed talkback port —
            # decline rather than leave the caller ringing forever
            log.warning("media bridge init failed (%s) - declining call", e)
            self.reply(call, self.in_dialog_response(call.invite, "486 Busy Here", extra_to_tag=call.to_tag))
            call.state = "closed"
            return
        call.local.start()
        if call.state != "early":
            return call.local.stop()
        ok = self.in_dialog_response(call.invite, "200 OK", ctype="application/sdp", extra_to_tag=call.to_tag)
        ok.body = self._local_answer_sdp(call, talkback)
        self.reply(call, ok)
        call.state = "established"
        self._fire_event("answered", call)
        self._log_local_answer(call, talkback, intercom)

    def _should_answer(self, call):
        subject = (call.invite.get("Subject") or "").lower()
        if self.o.auto_answer == "off" or (self.o.auto_answer == "extension" and "extension" not in subject):
            log.info(
                "ringing only (auto-answer=%s, subject=%r) - waiting for the caller to CANCEL/BYE",
                self.o.auto_answer,
                subject,
            )
            return False
        return True

    def _local_media(self, call, door_a, door_v, remote_sdp, talkback, intercom):
        if intercom:
            return IntercomMedia(self.o, self.rtsp, remote_sdp["ip"] or call.addr[0])
        ip = remote_sdp["ip"]
        audio = (
            (door_a["ip"] or ip, door_a["port"]) if door_a else (ip, self.o.talkback_port) if talkback and ip else None
        )
        video = (door_v["ip"] or ip, door_v["port"]) if door_v else None
        return LocalMediaBridge(self.o, audio, video, fixed=talkback)

    def _local_answer_sdp(self, call, talkback):
        if talkback:
            return sdp_answer_talkback(self.o.serial, self.o.local_ip, self.o.talkback_port, opts=self.o)
        return sdp_answer_firmware(self.o.username, self.o.local_ip, call.local.audio_port, call.local.video_port)

    def _log_local_answer(self, call, talkback, intercom):
        on = lambda p: "ON" if p else "OFF"
        log.info(
            "local call answered (%s) — speaker %s, mic %s, video %s; peer audio %s video %s",
            (
                f"intercom RTSP :{self.o.rtsp_port}"
                if intercom
                else f"talkback RTP :{self.o.talkback_port}"
                if talkback
                else "negotiated RTP"
            ),
            on(call.local.aplay),
            on(call.local.arecord),
            on(call.local.ffplay),
            call.local.door_a,
            call.local.door_v,
        )

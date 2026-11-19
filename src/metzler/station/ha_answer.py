"""Embedded/HA mode (vdm10-ha): explicit answer + hangup for calls parked by ha_mode."""

import logging

from metzler.sip.sdp import parse_sdp, sdp_answer_firmware, sdp_answer_talkback
from metzler.station.ha_media import HaCallMedia, HaIntercomMedia

log = logging.getLogger("indoor")


class HaAnswerMixin:
    """answer_call()/hangup_call() for calls parked in state 'early' by the
    ha_mode branch in on_invite. The 200 OK reuses LocalAnswerMixin's dialect
    (header-only talkback for intercom offers, firmware m= lines for door calls)
    but binds HaCallMedia/HaIntercomMedia instead of ALSA media."""

    def answer_call(self, cid):
        """answer the parked call `cid`. Returns False when there is no ringing
        call with that Call-ID (e.g. the caller already cancelled)."""
        call = self.calls.get(cid)
        # "early" = parked only; "bridging" = parked + a trunk leg is racing
        if call is None or call.state not in ("early", "bridging") or not self.running:
            return False
        remote_sdp = parse_sdp(call.invite.body)
        talkback = not remote_sdp["media"]  # hicore intercom offers carry no m= lines
        door_a = next((m for m in remote_sdp["media"] if m["type"] == "audio"), None)
        door_v = next((m for m in remote_sdp["media"] if m["type"] == "video"), None)
        intercom = talkback and self.rtsp
        try:
            call.local = self._ha_media(call, door_a, door_v, remote_sdp, talkback, intercom)
        except OSError as e:
            # e.g. a prior call's media still holds the fixed talkback port
            log.warning("ha media init failed (%s) - declining call", e)
            self.reply(call, self.in_dialog_response(call.invite, "486 Busy Here", extra_to_tag=call.to_tag))
            call.state = "closed"
            return False
        call.local.start()
        if call.state not in ("early", "bridging"):  # a CANCEL/BYE raced the answer
            call.local.stop()
            return False
        ok = self.in_dialog_response(call.invite, "200 OK", ctype="application/sdp", extra_to_tag=call.to_tag)
        ok.body = self._ha_answer_sdp(call, talkback)
        self.reply(call, ok)
        # HA won the race against the trunk legs - CANCEL them before marking
        # the call established (teardown keys BYE vs CANCEL off call.state)
        self._end_provider_leg(call)
        call.state = "established"
        self._end_provider_leg(call)  # ha_mode + trunk: CANCEL the racing legs
        self._fire_event("answered", call)
        log.info("ha call answered (%s, %s)", "intercom-rtsp" if intercom else "rtp", call.id)
        return True

    def _ha_media(self, call, door_a, door_v, remote_sdp, talkback, intercom):
        """the HA-media counterpart of LocalAnswerMixin._local_media"""
        if intercom:
            return HaIntercomMedia(self.rtsp, getattr(self.o, "ha_mic_feed", None))
        ip = remote_sdp["ip"]
        audio = (
            (door_a["ip"] or ip, door_a["port"]) if door_a else (ip, self.o.talkback_port) if talkback and ip else None
        )
        video = (door_v["ip"] or ip, door_v["port"]) if door_v else None
        return HaCallMedia(
            self.o,
            audio,
            video,
            fixed=talkback,
            mic_feed=getattr(self.o, "ha_mic_feed", None),
            on_audio=getattr(self.o, "ha_on_audio", None),
        )

    def _ha_answer_sdp(self, call, talkback):
        if talkback:
            return sdp_answer_talkback(self.o.serial, self.o.local_ip, self.o.talkback_port, opts=self.o)
        return sdp_answer_firmware(self.o.username, self.o.local_ip, call.local.audio_port, call.local.video_port)

    def hangup_call(self, cid):
        """decline a ringing call (final response clearing the registrar's busy
        flag - 486 via the :5065 registrar, 487 on a direct leg) or BYE an
        established one, then close it. Returns False without such a call."""
        call = self.calls.get(cid)
        if call is None:
            return False
        if call.state == "established":
            self.bye_intercom(call)
            call.end_reason = "bye"
        else:
            code = "486 Busy Here" if ":5065" in (call.invite.get("Via") or "") else "487 Request Terminated"
            self.reply(call, self.in_dialog_response(call.invite, code, extra_to_tag=call.to_tag))
            call.end_reason = "declined"
        self._end_provider_leg(call)  # stop any trunk legs still ringing
        self._close_call(call, cid)
        return True

"""Incoming intercom/door call -> SIP provider bridge."""

import logging
import threading

from metzler.media.intercom import SipIntercomMedia
from metzler.media.rtp import RtpRelay
from metzler.provider import SipProvider
from metzler.sip.sdp import (
    media_addr,
    parse_sdp,
    sdp_answer_firmware,
    sdp_answer_talkback,
)
from metzler.station.call import Call

log = logging.getLogger("indoor")


class ProviderBridgeMixin:
    """Incoming intercom/door call -> SIP provider bridge."""

    def on_invite(self, pkt, addr):
        sdp = parse_sdp(pkt.body)
        log.info("INVITE from %s: From=%s Subject=%r sdp-ip=%s media-lines=%d",
                 addr, pkt.get("From"), pkt.get("Subject"), sdp["ip"], len(sdp["media"]))
        if self._provider_loop_guard(pkt, addr):
            return
        if self.local_mode:
            log.info("incoming call (headset mode, auto-answer=%s)", self.o.auto_answer)
        else:
            log.info("incoming call INVITE from %s — bridging via %s:%d to %s",
                     addr, *self.provider.proxy, self.o.sip_target)
        self.send(self.in_dialog_response(pkt, "100 Trying"), addr)

        call = Call(self, pkt, addr)
        self.calls[call.id] = call

        ringing = self.in_dialog_response(pkt, "180 Ringing", extra_to_tag=call.to_tag)
        ringing.set("Allow-Events", "talk, hold")
        self.send(ringing, addr)

        remote_sdp = parse_sdp(pkt.body)
        door_a = next((m for m in remote_sdp["media"] if m["type"] == "audio"), None)
        door_v = next((m for m in remote_sdp["media"] if m["type"] == "video"), None)
        if self.local_mode:
            threading.Thread(target=self.answer_locally, args=(call, door_a, door_v, remote_sdp),
                             daemon=True).start()
            return
        self._attach_bridge_media(call, remote_sdp, door_a, door_v)
        self.provider.invite(call)

    def _provider_loop_guard(self, pkt, addr):
        """True if this INVITE must not be bridged to the provider (handled here)"""
        if not self.provider:
            return False
        # our own provider call forked back at us (the proxy rings every contact
        # on the account, including ours). Silently ignore it: a non-responding
        # branch keeps the call in "trying" state, giving pushed mobile devices
        # time to wake and register (a 486 would end the call as busy instantly)
        if self.provider.owns(pkt.get("Call-ID")):
            log.debug("ignoring our own provider fork (Call-ID %s)", pkt.get("Call-ID"))
            return True
        # an inbound call from the SIP side (someone dialing the account) must
        # never be bridged back to the same account - that is the loop
        if self.provider.is_provider_addr(addr):
            log.info("declining inbound provider call from %s", pkt.get("From"))
            self.send(self.in_dialog_response(pkt, "486 Busy Here"), addr)
            return True
        return False

    def _attach_bridge_media(self, call, remote_sdp, door_a, door_v):
        """door calls relay RTP on both legs; intercom calls pull the caller's
        RTSP PCMU and expose the SIP peer's audio on our :554"""
        if door_a:
            call.kind = "door"
            call.audio = RtpRelay(); call.video = RtpRelay()
            call.audio.start(); call.video.start()
            call.audio.set_peer(a=(door_a["ip"] or remote_sdp["ip"], door_a["port"]))
            if door_v:
                call.video.set_peer(a=(door_v["ip"] or remote_sdp["ip"], door_v["port"]))
        else:
            call.kind = "intercom"
            call.media = SipIntercomMedia(self.o, self.rtsp, remote_sdp["ip"] or call.addr[0])

    def provider_answered(self, call, resp):
        """provider leg got 200 OK: answer the indoor caller in its own dialect,
        ACK the provider, then connect the media"""
        resp_sdp = parse_sdp(resp.body)
        prov_a = media_addr(resp_sdp, "audio")
        ok = self.in_dialog_response(call.invite, "200 OK", ctype="application/sdp",
                                     extra_to_tag=call.to_tag)
        ok.body = self._indoor_answer(call, resp_sdp, prov_a)
        self.send(ok, call.addr)
        self.provider.ack(call, resp)
        call.state = "established"
        self._bind_srtp(call, resp_sdp)
        self._connect_media(call, prov_a)

    def _bind_srtp(self, call, resp_sdp):
        streams = ([("audio", call.media)] if call.kind == "intercom"
                   else [("audio", call.audio), ("video", call.video)])
        modes = {kind: SipProvider.bind_srtp(media, next(
                     (m for m in resp_sdp["media"] if m["type"] == kind and m["port"]), None))
                 for kind, media in streams}
        log.info("provider media protection: %s",
                 ", ".join(f"{k}={v}" for k, v in modes.items()))

    def _indoor_answer(self, call, resp_sdp, prov_a):
        """the caller's 200 OK body in its own dialect: header-only with the
        private attrs for intercom calls, firmware-style m= lines for door calls"""
        if call.kind == "intercom":
            return sdp_answer_talkback(self.o.serial, self.o.local_ip, opts=self.o)
        prov_v = media_addr(resp_sdp, "video")
        if prov_a: call.audio.set_peer(b=prov_a)
        if prov_v: call.video.set_peer(b=prov_v)
        return sdp_answer_firmware(self.o.username, self.o.local_ip,
                                   call.audio.port_a, call.video.port_a)

    def _connect_media(self, call, prov_a):
        if call.kind == "intercom":
            # SipIntercomMedia feeds provider audio to rtsp.mic; the caller
            # pulls :554 and hears the SIP peer
            if prov_a: call.media.set_provider(prov_a)
            call.media.start()
            log.info("call established: intercom <-> SIP %s (rtsp :%d, peer %s)",
                     self.o.sip_domain, self.o.rtsp_port, prov_a)
        else:
            log.info("call established: door <-> SIP %s (audio %d<->%s, video %d<->%s)",
                     self.o.sip_domain, call.audio.port_a, call.audio.peer_b,
                     call.video.port_a, call.video.peer_b)

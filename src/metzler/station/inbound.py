"""Inbound provider call (the SIP app dials us) -> outbound intercom call.

The reverse of ProviderBridgeMixin: the app's INVITE rings --inbound-target
on the intercom; when it answers, a SipIntercomMedia bridges the legs
(pull the callee's :554 -> app RTP, app RTP -> our :554 mic the callee pulls).
"""

import logging
import threading

from metzler.media.intercom import SipIntercomMedia
from metzler.media.srtp import SrtpSession, srtp_available
from metzler.sip.sdp import media_addr, parse_sdp
from metzler.station.call import Call

log = logging.getLogger("indoor")


class InboundCallMixin:
    """Inbound provider call (the SIP app dials us) -> outbound intercom call."""

    def inbound_provider_call(self, pkt, addr, trunk):
        """a call from the SIP side (someone dialed the provider account or
        the fritz extension): ring --inbound-target on the intercom and bridge
        both legs when it answers. Without a target we must decline -
        bridging it back to the SIP side would loop."""
        if not self.o.inbound_target or self.local_mode:
            log.info("declining inbound %s call from %s", trunk.label, pkt.get("From"))
            self.send(self.in_dialog_response(pkt, "486 Busy Here"), addr)
            return
        self.send(self.in_dialog_response(pkt, "100 Trying"), addr)
        call = Call(self, pkt, addr)
        call.inbound = True
        call.trunk = trunk  # the SIP leg this call came from
        call.kind = "intercom"
        self.calls[call.id] = call
        # the inbound leg is the session HA surfaces ("ringing the indoor"):
        # call_out's intercom leg is an implementation detail, like a trunk leg
        self._fire_event("ring", call)
        self.reply(call, self.in_dialog_response(pkt, "180 Ringing", extra_to_tag=call.to_tag))
        log.info("inbound %s call %s -> ringing intercom %s", trunk.label, pkt.get("From"), self.o.inbound_target)
        if self.registered.is_set():
            return self._place_inbound_call(call)
        threading.Thread(target=self._place_inbound_call, args=(call,), daemon=True).start()

    def _place_inbound_call(self, call):
        """dial the intercom target; call_out blocks until REGISTERED, so it
        may run on a thread. Failing that, tell the app instead of leaving
        it ringing forever."""
        if not self.call_out(self.o.inbound_target, bridged=call):
            self.reply(call, self.in_dialog_response(call.invite, "503 Service Unavailable", extra_to_tag=call.to_tag))
            self._close_call(call, call.id)
        elif call.id not in self.calls:
            # the app hung up while we were still waiting for registration -
            # drop the just-placed intercom call instead of ghost-ringing it
            self.bye_outbound(call.out_cid)

    def inbound_bridged_answered(self, out, resp_sdp, addr):
        """the intercom callee answered our --inbound-target call: bind a
        SipIntercomMedia to it and 200 OK the app (offer/answer SDP, SRTP
        only when the app offered it)"""
        call = out["bridged"]
        offer = parse_sdp(call.invite.body)
        media = SipIntercomMedia(self.o, self.rtsp, resp_sdp["ip"] or addr[0])
        offer_a = media_addr(offer, "audio")
        if offer_a:
            media.set_provider(offer_a)
        media.srtp = self._inbound_srtp(call, offer)
        call.media = media
        ok = self.in_dialog_response(call.invite, "200 OK", ctype="application/sdp", extra_to_tag=call.to_tag)
        ok.body = call.trunk.answer_sdp(offer, media)
        self.reply(call, ok)
        call.state = "established"
        self._fire_event("answered", call)
        media.start()
        log.info(
            "inbound call established: SIP app <-> intercom %s (peer audio %s, protection %s)",
            out["num"],
            offer_a,
            "srtp" if media.srtp else "rtp",
        )

    def _inbound_srtp(self, call, offer):
        """SDES session for the inbound SIP leg when the caller's audio offer
        is RTP/SAVP and the trunk encrypts (fritz never does); None = plain RTP"""
        m = next((x for x in offer["media"] if x["type"] == "audio"), None)
        if not (call.trunk.o.sip_srtp and srtp_available() and m and "SAVP" in m.get("proto", "")):
            return None
        s = SrtpSession()
        return s if s.accept_answer(m) else None

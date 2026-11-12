"""Incoming intercom/door call -> SIP provider bridge."""

import logging
import re
import threading

from metzler.media.intercom import SipIntercomMedia
from metzler.media.rtp import RtpRelay
from metzler.provider import SipProvider
from metzler.sip.message import userpart
from metzler.sip.sdp import (
    media_addr,
    parse_sdp,
    sdp_answer_firmware,
    sdp_answer_talkback,
    telephone_event_pt,
)
from metzler.station.call import Call

log = logging.getLogger("indoor")


class ProviderBridgeMixin:
    """Incoming intercom/door call -> SIP provider bridge."""

    def on_invite(self, pkt, addr):
        sdp = parse_sdp(pkt.body)
        log.info(
            "INVITE from %s: From=%s Subject=%r sdp-ip=%s media-lines=%d",
            addr,
            pkt.get("From"),
            pkt.get("Subject"),
            sdp["ip"],
            len(sdp["media"]),
        )
        if self._retransmitted_invite(pkt, addr):
            return
        if self._provider_loop_guard(pkt, addr):
            return
        self._log_incoming(pkt, addr)
        self.send(self.in_dialog_response(pkt, "100 Trying"), addr)

        call = Call(self, pkt, addr)
        self.calls[call.id] = call

        ringing = self.in_dialog_response(pkt, "180 Ringing", extra_to_tag=call.to_tag)
        ringing.set("Allow-Events", "talk, hold")
        self.reply(call, ringing)

        remote_sdp = parse_sdp(pkt.body)
        door_a = next((m for m in remote_sdp["media"] if m["type"] == "audio"), None)
        door_v = next((m for m in remote_sdp["media"] if m["type"] == "video"), None)
        if self._park_call(call, pkt, door_a, door_v, remote_sdp):
            return
        self._attach_bridge_media(call, remote_sdp, door_a, door_v)
        log.info("call classified: %s", "door (video to provider)" if call.door else "intercom (audio only)")
        if self.provider:
            call.trunk = self.provider  # prov_* describes the provider leg until the fritz leg wins
            self.provider.invite(call)
        if self.fritz and self.o.fritz_target:
            self.fritz.invite(call)  # --fritz: the fritzfon races every bridged call

    def _log_incoming(self, pkt, addr):
        """mode banner for an accepted INVITE: headset / ha_mode / provider bridging"""
        if self.local_mode:
            log.info("incoming call (headset mode, auto-answer=%s)", self.o.auto_answer)
        elif getattr(self.o, "ha_mode", False) and not (self.provider or self.fritz):
            log.info("incoming call (ha_mode - parking for explicit HA answer)")
        elif getattr(self.o, "ha_mode", False):
            log.info("incoming call (ha_mode + trunk race - first answer wins)")
        else:
            log.info(
                "incoming call INVITE from %s — bridging via %s:%d to %s", addr, *self.provider.proxy, self.o.sip_target
            )

    def _park_call(self, call, pkt, door_a, door_v, remote_sdp):
        """non-trunk paths: ha_mode parks the call ringing for an explicit
        answer_call/hangup_call (no provider leg, no local answer - note that
        auto_answer="off" alone only gates answer_locally inside local_mode,
        so without this branch the no-provider path would hit provider.invite);
        local_mode hands the call to answer_locally on a thread.
        Returns True when the call was handled and must NOT be bridged."""
        if getattr(self.o, "ha_mode", False):
            call.door = bool(door_a) or self._is_door_call(pkt)
            call.kind = "door" if door_a else "intercom"
            self._fire_event("ring", call)
            if not (self.provider or (self.fritz and self.o.fritz_target)):
                log.info(
                    "call %s parked ringing (ha_mode, %s) - awaiting explicit answer",
                    call.id,
                    "door" if call.door else "intercom",
                )
                return True
            # ha_mode + trunk(s): park for HA *and* ring the provider/fritz
            # legs - whichever side 200-OKs first wins, the loser is CANCELed
            # (provider win -> provider_answered; HA win -> answer_call +
            # _end_provider_leg). Fall through to the bridge path.
            log.info(
                "call %s parked ringing + racing trunk legs (ha_mode, %s)",
                call.id,
                "door" if call.door else "intercom",
            )
            return False
        if self.local_mode:
            threading.Thread(target=self.answer_locally, args=(call, door_a, door_v, remote_sdp), daemon=True).start()
            return True
        return False

    def _provider_loop_guard(self, pkt, addr):
        """True if this INVITE must not be bridged to the provider (handled here)"""
        if not (self.provider or self.fritz):
            return False
        # our own provider call forked back at us (the proxy rings every contact
        # on the account, including ours). Silently ignore it: a non-responding
        # branch keeps the call in "trying" state, giving pushed mobile devices
        # time to wake and register (a 486 would end the call as busy instantly)
        if self.provider and self.provider.owns(pkt.get("Call-ID")):
            log.debug("ignoring our own provider fork (Call-ID %s)", pkt.get("Call-ID"))
            return True
        # same for a racing leg to the fritzfon: we registered as its SIP
        # account, so the box forks our own INVITE right back at us
        if self.fritz and self.fritz.owns(pkt.get("Call-ID")):
            log.debug("ignoring our own fritz fork (Call-ID %s)", pkt.get("Call-ID"))
            return True
        # an inbound call from either SIP side (someone dialing the provider
        # account, or a fritz phone calling our extension) is bridged to
        # --inbound-target or declined (486 inside) - the trunk object is
        # remembered on the call so its SDP answers use that leg's identity
        for trunk in (self.provider, self.fritz):
            if trunk and trunk.is_provider_addr(addr):
                self.inbound_provider_call(pkt, addr, trunk)
                return True
        return False

    def _retransmitted_invite(self, pkt, addr):
        """True if this INVITE is a UDP retransmit of a transaction we already
        serve - resend the last response instead of leaking a second Call
        (and its media threads) on top of the first"""
        call = self.calls.get(pkt.get("Call-ID"))
        if call:
            self.send(call.last_resp or self.in_dialog_response(pkt, "100 Trying"), addr)
            return True
        resp = self.ended_resp(pkt.get("Call-ID"))
        if resp is not None:
            self.send(resp, addr)
            return True
        return False

    def _attach_bridge_media(self, call, remote_sdp, door_a, door_v):
        """door calls relay RTP on both legs; intercom calls pull the caller's
        RTSP PCMU and expose the SIP peer's audio on our :554. An outdoor
        caller (door call over the header-only dialect) additionally gets a
        video leg forwarding the door's pulled H264 to the provider."""
        call.door = bool(door_a) or self._is_door_call(call.invite)
        if door_a:
            call.kind = "door"
            call.audio = RtpRelay()
            call.video = RtpRelay()
            call.audio.start()
            call.video.start()
            call.audio.set_peer(a=(door_a["ip"] or remote_sdp["ip"], door_a["port"]))
            if door_v:
                call.video.set_peer(a=(door_v["ip"] or remote_sdp["ip"], door_v["port"]))
        else:
            call.kind = "intercom"
            call.media = SipIntercomMedia(self.o, self.rtsp, remote_sdp["ip"] or call.addr[0], video=call.door)
            call.media.on_dtmf = lambda d, c=call: self._dtmf_digit(c, d)

    @staticmethod
    def _caller_class(pkt):
        """caller number class, firmware FUN_000f9ce0: '1000000000N' -> 3
        (room/extension); else sscanf('%01d%03d%02d%03d%02d') -> f3!=0 && f4==0
        -> 1 (door/outdoor station). Other classes collapse to 0."""
        num = userpart(pkt.get("From") or "")
        if num.startswith("1000000000"):
            return 3
        if len(num) == 11 and num.isdigit() and int(num[4:6]) != 0 and int(num[6:9]) == 0:
            return 1
        return 0

    @classmethod
    def _is_door_call(cls, pkt):
        """call originated by an outdoor station. Firmware signals, any of:
        caller number class 1; Subject 'call to app' (vs 'call to extension');
        a=doorType:>0 in the header-only offer. A class-3 caller (room/
        extension unit) is NEVER a door - the main indoor stamps doorType:1
        on extension calls too, and those must stay audio-only."""
        cls_ = cls._caller_class(pkt)
        if cls_ == 3:
            return False
        if cls_ == 1:
            return True
        if "call to app" in (pkt.get("Subject") or "").lower():
            return True
        m = re.search(r"a=doorType:(\d+)", pkt.body)
        return bool(m and int(m.group(1)))

    def provider_answered(self, call, resp):
        """provider leg got 200 OK: answer the indoor caller in its own dialect,
        ACK the provider, then connect the media (already running if a 18x
        early-media response primed it)"""
        resp_sdp = parse_sdp(resp.body)
        prov_a = media_addr(resp_sdp, "audio")
        ok = self.in_dialog_response(call.invite, "200 OK", ctype="application/sdp", extra_to_tag=call.to_tag)
        ok.body = self._indoor_answer(call, resp_sdp, prov_a)
        self.reply(call, ok)
        (call.trunk or self.provider).ack(call, resp)  # the winning leg's trunk
        call.prov_established = True
        call.state = "established"
        self._fire_event("answered", call)
        if not call.provider_media:
            self._bind_srtp(call, resp_sdp)
            self._connect_media(call, prov_a, media_addr(resp_sdp, "video"))

    def early_media(self, call, resp):
        """a 18x response carrying SDP (183 Session Progress or 180+SDP):
        the callee's media address is known — latch it and start streaming.
        Door video reaches the app while it still rings; the indoor leg keeps
        ringing until the provider's 200 OK."""
        if call.provider_media:
            return
        resp_sdp = parse_sdp(resp.body)
        prov_a, prov_v = media_addr(resp_sdp, "audio"), media_addr(resp_sdp, "video")
        log.info("early media on provider leg (%s): audio %s, video %s", resp.status_line, prov_a, prov_v)
        self._bind_srtp(call, resp_sdp)
        self._connect_media(call, prov_a, prov_v)

    def _bind_srtp(self, call, resp_sdp):
        if call.kind == "intercom":
            streams = [("audio", call.media)]
            if call.media.video:
                streams.append(("video", call.media.video))
        else:
            streams = [("audio", call.audio), ("video", call.video)]
        modes = {
            kind: SipProvider.bind_srtp(
                media, next((m for m in resp_sdp["media"] if m["type"] == kind and m["port"]), None)
            )
            for kind, media in streams
        }
        log.info("provider media protection: %s", ", ".join(f"{k}={v}" for k, v in modes.items()))
        if call.kind == "intercom":
            pt = telephone_event_pt(resp_sdp)
            call.media.dtmf_pt = pt
            log.info("provider telephone-event: %s", f"PT {pt}" if pt is not None else "not negotiated")

    def _indoor_answer(self, call, resp_sdp, prov_a):
        """the caller's 200 OK body in its own dialect: header-only with the
        private attrs for intercom calls, firmware-style m= lines for door calls"""
        if call.kind == "intercom":
            return sdp_answer_talkback(self.o.serial, self.o.local_ip, opts=self.o)
        return sdp_answer_firmware(self.o.username, self.o.local_ip, call.audio.port_a, call.video.port_a)

    def _connect_media(self, call, prov_a, prov_v):
        """latch the provider media peers and start the media threads"""
        call.provider_media = True
        if call.kind == "intercom":
            # SipIntercomMedia feeds provider audio to rtsp.mic; the caller
            # pulls :554 and hears the SIP peer
            if prov_a:
                call.media.set_provider(prov_a)
            if call.media.video:
                call.media.video.peer = prov_v  # None kills the leg (fritz win)
            call.media.start()
            log.info(
                "%s media streaming (rtsp :%d, peer audio %s video %s)",
                "door" if call.door else "intercom",
                self.o.rtsp_port,
                prov_a,
                prov_v,
            )
        else:
            if prov_a:
                call.audio.set_peer(b=prov_a)
            if prov_v:
                call.video.set_peer(b=prov_v)
            log.info(
                "call established: door <-> SIP %s (audio %d<->%s, video %d<->%s)",
                self.o.sip_domain,
                call.audio.port_a,
                call.audio.peer_b,
                call.video.port_a,
                call.video.peer_b,
            )

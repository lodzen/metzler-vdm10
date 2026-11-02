"""The station's SIP endpoint: socket, RTSP server, packet helpers, the
receive loop and request routing, run/shutdown."""

import logging
import re
import shutil
import signal
import socket
import threading
import time
from typing import ClassVar

from metzler.media.rtsp_server import RtspServer
from metzler.provider import FritzTrunk, SipProvider
from metzler.sip.message import UA, Packet, branch, tag, uri_of

log = logging.getLogger("indoor")


class StationCore:
    """The station's SIP endpoint: socket, RTSP server, packet helpers, the
    receive loop and request routing, run/shutdown."""

    def __init__(self, o):
        self.o = o
        self.server = (o.server, o.server_port)
        self.sip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sip.bind(("0.0.0.0", o.listen_port))
        self.bind_port = self.sip.getsockname()[1]
        self.expires = o.expires
        self.init_registrations()  # self.regs + self.registered (primary ext)
        self.calls = {}  # intercom/door-side Call-ID -> Call
        self.ended_invites = {}  # ended Call-ID -> (final resp, ts); retransmit answers
        self.out_calls = {}  # our outbound Call-ID -> dict
        self.running = True
        self.local_tag = tag()
        self.local_mode = bool(o.local_audio or o.local_mic or o.local_tone or o.video_player)
        # standard-SIP trunk to the provider (None in local headset mode)
        self.provider = SipProvider(self, o) if (o.sip_host and not self.local_mode) else None
        # --fritz: second, inbound-only trunk - REGISTERs as an IP phone at a
        # Fritz!Box; its calls reach us on this same socket, told apart from
        # intercom/provider traffic by the sender's address (is_provider_addr)
        self.fritz = FritzTrunk(self, o) if (o.fritz and not self.local_mode) else None
        self.rtsp = None
        # the intercom RTSP server is needed in BOTH modes: local (headset mic)
        # and provider (the caller pulls :554 to receive the SIP peer's audio)
        try:
            self.rtsp = RtspServer(o)
            self.rtsp.start()
            log.info("intercom RTSP server listening on %s:%d", o.local_ip, o.rtsp_port)
        except OSError as e:
            log.warning("cannot bind RTSP :%d (%s) - caller can't pull our stream", o.rtsp_port, e)

    def send(self, pkt, addr):
        pkt.set("Content-Length", str(len(pkt.body)), True)
        data = str(pkt).encode()
        self.sip.sendto(data, addr)
        log.debug(">> %s:%d\n%s", addr[0], addr[1], data.decode(errors="replace"))

    def via(self):
        return f"SIP/2.0/UDP {self.o.local_ip}:{self.bind_port};rport;branch={branch()}"

    def contact(self):
        return f"<sip:{self.o.username}@{self.o.local_ip}:{self.bind_port}>"

    def base_request(self, method, uri, to_uri=None, frm_uri=None):
        p = Packet()
        p.status_line = f"{method} {uri} SIP/2.0"
        p.set("Via", self.via())
        p.set("Max-Forwards", "70")
        p.set("Contact", self.contact())
        p.set("To", to_uri or f"<sip:{self.o.username}@{self.o.server}:{self.o.server_port}>")
        p.set(
            "From",
            frm_uri
            or f'"{self.o.name}"<sip:{self.o.username}@{self.o.server}:{self.o.server_port}>;tag={self.local_tag}',
        )
        p.set("User-Agent", UA)
        p.set("Allow", "INVITE, ACK, CANCEL, BYE, REFER, INFO, OPTIONS, MESSAGE")
        return p

    def in_dialog_response(self, req, code, body="", ctype=None, extra_to_tag=None):
        p = Packet()
        p.status_line = f"SIP/2.0 {code}"
        for v in req.get_all("Via"):
            p.set("Via", v)
        for v in req.get_all("Record-Route"):
            p.set("Record-Route", v)
        to = req.get("To")
        if extra_to_tag and "tag=" not in to:
            to += f";tag={extra_to_tag}"
        p.set("To", to)
        p.set("From", req.get("From"))
        p.set("Call-ID", req.get("Call-ID"))
        p.set("CSeq", req.get("CSeq"))
        p.set("Contact", self.contact())
        p.set("User-Agent", UA)
        if ctype:
            p.set("Content-Type", ctype)
        p.body = body
        return p

    def reply(self, call, pkt):
        """answer call.invite and remember the response - a retransmitted
        INVITE gets it resent (see _retransmitted_invite)"""
        call.last_resp = pkt
        self.send(pkt, call.addr)

    def _close_call(self, call, cid):
        """stop the call and drop it from self.calls; its final response is
        kept ~64s (INVITE client transaction lifetime) so a late retransmit
        still gets answered instead of spawning a ghost call"""
        if call.last_resp is not None:
            self.ended_invites[cid] = (call.last_resp, time.time())
        call.stop()
        self.calls.pop(cid, None)

    def ended_resp(self, cid):
        """the final response of a just-ended call, or None; lazily prunes"""
        now = time.time()
        for old in [c for c, (_, ts) in self.ended_invites.items() if now - ts > 64]:
            del self.ended_invites[old]
        ent = self.ended_invites.get(cid)
        return ent[0] if ent else None

    RING_TIMEOUT: ClassVar[int] = 120  # s a call may ring before the janitor ends it

    def _reaper_loop(self):
        """safety net for callers that die without CANCEL/BYE (station crash,
        network drop): a call still not established after RING_TIMEOUT is torn
        down - 408 to the caller (clears the registrar's busy flag), CANCEL on
        the live legs, media stopped. Established calls are never reaped."""
        while self.running:
            time.sleep(30)
            self._reap_once(time.time())

    def _reap_once(self, now):
        for cid, call in list(self.calls.items()):
            if call.state == "established" or now - call.created <= self.RING_TIMEOUT:
                continue
            log.warning("reaping stale call %s (state %s, age %ds)", cid, call.state, now - call.created)
            self.reply(call, self.in_dialog_response(call.invite, "408 Request Timeout", extra_to_tag=call.to_tag))
            self._end_provider_leg(call)
            self._close_call(call, cid)
        for cid, out in list(self.out_calls.items()):
            if now - out.get("ts", now) > self.RING_TIMEOUT:
                log.warning("reaping stale outbound call to %s", out["num"])
                self.bye_outbound(cid)

    # request method -> handler name; ACK needs no action (our 200s are not retransmitted)
    REQUEST_HANDLERS: ClassVar[dict] = {
        "OPTIONS": "on_options",
        "MESSAGE": "on_message",
        "INVITE": "on_invite",
        "CANCEL": "on_cancel",
        "BYE": "on_bye",
        "INFO": "on_info",
        "ACK": None,
    }

    def listener(self):
        while self.running:
            try:
                data, addr = self.sip.recvfrom(65535)
            except OSError:
                return
            self.handle_datagram(data, addr)

    def handle_datagram(self, data, addr):
        """parse one UDP datagram and route it (REGISTER replies go to the
        waiting registration loops, everything else to its handler)"""
        text = data.decode(errors="replace")
        try:
            pkt = Packet.parse(text)
        except (ValueError, IndexError, AttributeError) as e:
            return log.debug("unparseable from %s: %s", addr, e)
        log.debug("<< %s:%d\n%s", addr[0], addr[1], text)
        sl = pkt.status_line
        if sl.startswith("SIP/2.0") and self._dispatch_reg_response(pkt):
            return
        name = "on_response" if sl.startswith("SIP/2.0") else self.REQUEST_HANDLERS.get(sl.split(" ", 1)[0])
        if not name:
            return
        try:
            getattr(self, name)(pkt, addr)
        except Exception:  # keep the rx loop alive on any handler bug
            log.exception("error handling %s", sl)

    def on_options(self, pkt, addr):
        self.send(self.in_dialog_response(pkt, "200 OK"), addr)

    def on_message(self, pkt, addr):
        log.info("MESSAGE from %s: %s", addr, pkt.body[:300])
        self.send(self.in_dialog_response(pkt, "200 OK"), addr)

    def on_info(self, pkt, addr):
        # DTMF-relay / locknumXML — could forward to the provider as INFO; just ack
        log.info("INFO body: %s", pkt.body[:200])
        self.send(self.in_dialog_response(pkt, "200 OK"), addr)

    def _dispatch_reg_response(self, pkt):
        """a REGISTER reply: hand it to whichever registration loop is
        waiting (intercom ext(s), provider, fritz) - matched by Call-ID"""
        for r in self.regs:
            if r.is_reg_response(pkt):
                r.reg_queue.put(pkt)
                return True
        for trunk in (self.provider, self.fritz):
            if trunk and trunk.is_reg_response(pkt):
                trunk.reg_queue.put(pkt)
                return True
        return False

    def _out_invite_response(self, out, pkt, addr):
        """response on an outbound intercom leg; a bridged app call gets the
        intercom's ringing and its final result forwarded"""
        cid, code = pkt.get("Call-ID"), int(pkt.status_line.split()[1])
        call = out.get("bridged")
        if code in (180, 183):
            log.info("outbound call %s ringing", out["num"])
            if call:  # the app hears the intercom ring
                self.reply(call, self.in_dialog_response(call.invite, "180 Ringing", extra_to_tag=call.to_tag))
        elif 200 <= code < 300:
            self.out_answered(out, pkt, addr)
        elif code >= 300:
            log.warning("outbound call to %s failed: %s", out["num"], pkt.status_line)
            self._ack_dead_final(pkt, addr)  # else the registrar retransmits ~30s
            self.out_calls.pop(cid, None)
            if call:  # callee busy/gone -> tell the app
                self.reply(call, self.in_dialog_response(call.invite, pkt.status_line[8:], extra_to_tag=call.to_tag))
                self._close_call(call, call.id)

    def on_response(self, pkt, addr):
        cseq = pkt.get("CSeq") or ""
        cid = pkt.get("Call-ID") or ""
        log.info("response %s (CSeq %s, Call-ID %s)", pkt.status_line, cseq, cid)
        out = self.out_calls.get(cid)
        if out and "INVITE" in cseq:
            return self._out_invite_response(out, pkt, addr)
        call = self.provider.calls.get(cid) if self.provider else None
        if call and "INVITE" in cseq:
            return self._provider_invite_response(call, pkt)
        if self.fritz and "INVITE" in cseq and cid in self.fritz.calls:
            return self._fritz_invite_response(self.fritz.calls[cid], pkt)
        if "INVITE" in cseq and int(pkt.status_line.split()[1]) >= 300:
            self._ack_dead_final(pkt, addr)  # leg already torn down - still ACK

    # explicit user rejections (decline on a ringing device) - unlike timeouts
    # or transport failures these kill the whole race, not just that leg
    DECLINE_CODES: ClassVar[frozenset] = frozenset((486, 603))

    def _ack_dead_final(self, pkt, addr):
        """a final response on an INVITE transaction we already dropped (a
        racing leg CANCELLed or declined earlier): it still needs the
        transaction ACK - our Via sits at the bottom of the response's list,
        so its branch is the last branch= - or the peer retransmits ~30s"""
        cseq, to = pkt.get("CSeq") or "", pkt.get("To")
        branches = re.findall(r"branch=([^;\s,]+)", pkt.get("Via") or "")
        if not to or not branches or "INVITE" not in cseq:
            return
        p = Packet()
        p.status_line = f"ACK {uri_of(to)} SIP/2.0"
        p.set("Via", f"SIP/2.0/UDP {self.o.local_ip}:{self.bind_port};rport;branch={branches[-1]}")
        p.set("Max-Forwards", "70")
        p.set("To", to)
        p.set("From", pkt.get("From"))
        p.set("Call-ID", pkt.get("Call-ID"))
        p.set("CSeq", cseq.replace("INVITE", "ACK"))
        p.set("User-Agent", UA)
        try:
            self.send(p, addr)
        except OSError:
            pass

    def _decline_call(self, call, pkt):
        """no leg left ringing, or a leg got explicitly declined: CANCEL any
        still-racing leg and give the indoor caller the final outcome. A
        401/407 challenge is never propagated - the indoor gets a plain 486."""
        if call.fritz_leg:
            self.fritz.cancel_leg(call)
            call.fritz_leg = None
        if self.provider and call.prov_call_id in self.provider.calls:
            self.provider.cancel(call)
            self.provider.calls.pop(call.prov_call_id, None)
        code = int(pkt.status_line.split()[1]) if pkt else 0
        # the registrar clears its global busy flag only on 408/486/603;
        # every other outcome (auth challenges, 488, 5xx) becomes a 486
        status = pkt.status_line[8:] if code in (408, 486, 603) else "486 Busy Here"
        self.reply(call, self.in_dialog_response(call.invite, status, extra_to_tag=call.to_tag))
        self._close_call(call, call.id)

    def _provider_invite_response(self, call, pkt):
        """response on the provider leg of a bridged call; on door calls a
        fritz leg may be racing it - the winner keeps the call, the loser is
        CANCELLed"""
        cid = pkt.get("Call-ID")
        result = self.provider.handle_response(call, pkt)
        if result == "answered":
            if call.fritz_leg:  # the app won the race
                self.fritz.cancel_leg(call)
                call.fritz_leg = None
            self.provider_answered(call, pkt)
        elif result == "early":
            self.early_media(call, pkt)
        elif result == "fail":
            self.provider.calls.pop(cid, None)
            if call.fritz_leg and int(pkt.status_line.split()[1]) not in self.DECLINE_CODES:
                return  # fritz still rings - keep waiting
            self._decline_call(call, pkt)

    def _fritz_invite_response(self, call, pkt):
        """response on the racing fritzfon leg of a door call"""
        cid = pkt.get("Call-ID")
        result = self.fritz.handle_response(call, pkt)
        if result == "answered":
            return self._fritz_won(call, pkt)
        if result == "fail":
            self.fritz.calls.pop(cid, None)
            call.fritz_leg = None
            prov_alive = self.provider and call.prov_call_id in self.provider.calls
            if not prov_alive or int(pkt.status_line.split()[1]) in self.DECLINE_CODES:
                self._decline_call(call, pkt)

    def _fritz_won(self, call, resp):
        """the fritzfon answered a door call first: CANCEL the still-racing
        provider leg, promote the fritz leg into the call's SIP-leg fields
        (ACK/teardown then work via call.trunk) and take the answered path.
        Idempotent - a retransmitted 200 gets re-ACKed only."""
        leg = call.fritz_leg
        if not leg:
            return self.fritz.ack(call, resp)
        if call.prov_call_id in self.provider.calls:
            self.provider.cancel(call)  # prov_* still = the provider leg
            self.provider.calls.pop(call.prov_call_id, None)
        (call.prov_call_id, call.prov_branch, call.prov_cseq, call.prov_from) = (
            leg["cid"],
            leg["branch"],
            leg["cseq"],
            leg["from"],
        )
        call.prov_to = call.prov_uri = None  # filled by ack() from the 200
        for m in (
            [call.media, getattr(call.media, "video", None)] if call.kind == "intercom" else [call.audio, call.video]
        ):
            if m:
                m.srtp = None  # fritz leg is plain RTP
        call.prov_plain = True
        call.provider_media = False  # rebind media to the fritz peer
        call.trunk = self.fritz
        call.fritz_leg = None
        self.provider_answered(call, resp)

    def run(self):
        threading.Thread(target=self.listener, daemon=True).start()
        for r in self.regs:
            threading.Thread(target=r.register_loop, daemon=True).start()
        threading.Thread(target=self.heartbeat_loop, daemon=True).start()
        threading.Thread(target=self._reaper_loop, daemon=True).start()
        if self.o.call:
            threading.Thread(target=self.call_out, args=(self.o.call,), daemon=True).start()
        if self.local_mode:
            missing = [
                t
                for t, need in (
                    ("aplay", self.o.local_audio),
                    ("arecord", self.o.local_mic),
                    ("ffplay", self.o.video_player),
                )
                if need and not shutil.which(t)
            ]
            if missing:
                log.warning(
                    "audio tools missing (%s) — install alsa-utils / ffmpeg, or run --list-audio to see devices",
                    ", ".join(missing),
                )
            log.info(
                "indoor station sim on %s:%d -> registrar %s:%d  [LOCAL headset mode: "
                "spk=%s mic=%s tone=%s video=%s dev=%s]",
                self.o.local_ip,
                self.bind_port,
                self.server[0],
                self.server[1],
                bool(self.o.local_audio),
                bool(self.o.local_mic),
                self.o.local_tone,
                bool(self.o.video_player),
                self.o.alsa_device,
            )
        else:
            self._start_trunks()

        def terminate(signum, frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, terminate)  # plain `kill` must unregister too
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def _start_trunks(self):
        """provider (+ optional fritz) REGISTER loops and the startup log"""
        if self.provider.needs_register():
            threading.Thread(target=self.provider.register_loop, daemon=True).start()
        else:
            log.info("no --sip-user/--sip-pass: trunk mode, provider REGISTER skipped")
        if self.fritz and self.fritz.needs_register():
            threading.Thread(target=self.fritz.register_loop, daemon=True).start()
        log.info(
            "indoor station sim on %s:%d -> registrar %s:%d, bridge via %s:%d to %s",
            self.o.local_ip,
            self.bind_port,
            self.server[0],
            self.server[1],
            self.provider.proxy[0],
            self.provider.proxy[1],
            self.o.sip_target,
        )

    def shutdown(self):
        """clean exit: end every call (BYE/CANCEL so the registrar's busy
        tracking clears), then Expires=0 to remove our dev_reg row —
        leaving stale state marks the whole system busy (486 to the door)"""
        self.running = False
        for cid in list(self.out_calls):
            try:
                self.bye_outbound(cid)
            except OSError:
                pass
        for call in list(self.calls.values()):
            self._end_call_on_shutdown(call)
        self.calls.clear()
        try:
            for r in self.regs:
                r.unregister()
            if self.provider and self.provider.needs_register():
                self.provider.unregister()
            if self.fritz and self.fritz.needs_register():
                self.fritz.unregister()
            time.sleep(0.3)
        except OSError:
            pass
        self.sip.close()

    def _end_call_on_shutdown(self, call):
        """established -> BYE the caller; still ringing -> a final response that
        clears the registrar's busy state. The provider leg is ended as well."""
        try:
            if call.state == "established":
                self.bye_intercom(call)
            else:
                # via the :5065 registrar only 408/603/486 clear its global busy flag (487 does not)
                code = "486 Busy Here" if ":5065" in (call.invite.get("Via") or "") else "487 Request Terminated"
                self.send(self.in_dialog_response(call.invite, code, extra_to_tag=call.to_tag), call.addr)
            self._end_provider_leg(call)
        except OSError:
            pass
        call.stop()

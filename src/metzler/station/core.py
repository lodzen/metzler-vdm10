"""The station's SIP endpoint: socket, RTSP server, packet helpers, the
receive loop and request routing, run/shutdown."""

import logging
import queue
import shutil
import signal
import socket
import threading
import time
from typing import ClassVar

from metzler.media.rtsp_server import RtspServer
from metzler.provider import SipProvider
from metzler.sip.message import UA, Packet, branch, call_id, tag

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
        self.reg = None                 # register packet (rebuilt each try)
        self.reg_call_id = call_id()
        self.reg_cseq = 1
        self.expires = o.expires
        self.registered = threading.Event()
        self.calls = {}                 # intercom/door-side Call-ID -> Call
        self.out_calls = {}             # our outbound Call-ID -> dict
        self.reg_queue = queue.Queue()  # responses matching reg_call_id
        self.running = True
        self.local_tag = tag()
        self.local_mode = bool(o.local_audio or o.local_mic or o.local_tone or o.video_player)
        # standard-SIP trunk to the provider (None in local headset mode)
        self.provider = SipProvider(self, o) if (o.sip_host and not self.local_mode) else None
        self.rtsp = None
        # the intercom RTSP server is needed in BOTH modes: local (headset mic)
        # and provider (the caller pulls :554 to receive the SIP peer's audio)
        try:
            self.rtsp = RtspServer(o)
            self.rtsp.start()
            log.info("intercom RTSP server listening on %s:%d", o.local_ip, o.rtsp_port)
        except OSError as e:
            log.warning("cannot bind RTSP :%d (%s) - caller can't pull our stream",
                        o.rtsp_port, e)

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
        p.set("From", frm_uri or
              f'"{self.o.name}"<sip:{self.o.username}@{self.o.server}:{self.o.server_port}>;tag={self.local_tag}')
        p.set("User-Agent", UA)
        p.set("Allow", "INVITE, ACK, CANCEL, BYE, REFER, INFO, OPTIONS, MESSAGE")
        return p

    def in_dialog_response(self, req, code, body="", ctype=None, extra_to_tag=None):
        p = Packet()
        p.status_line = f"SIP/2.0 {code}"
        for v in req.get_all("Via"): p.set("Via", v)
        for v in req.get_all("Record-Route"): p.set("Record-Route", v)
        to = req.get("To")
        if extra_to_tag and "tag=" not in to: to += f";tag={extra_to_tag}"
        p.set("To", to); p.set("From", req.get("From"))
        p.set("Call-ID", req.get("Call-ID")); p.set("CSeq", req.get("CSeq"))
        p.set("Contact", self.contact())
        p.set("User-Agent", UA)
        if ctype: p.set("Content-Type", ctype)
        p.body = body
        return p

    # request method -> handler name; ACK needs no action (our 200s are not retransmitted)
    REQUEST_HANDLERS: ClassVar[dict] = {"OPTIONS": "on_options", "MESSAGE": "on_message", "INVITE": "on_invite",
                                        "CANCEL": "on_cancel", "BYE": "on_bye", "INFO": "on_info", "ACK": None}

    def listener(self):
        while self.running:
            try: data, addr = self.sip.recvfrom(65535)
            except OSError: return
            self.handle_datagram(data, addr)

    def handle_datagram(self, data, addr):
        """parse one UDP datagram and route it (REGISTER replies go to the
        waiting registration loops, everything else to its handler)"""
        text = data.decode(errors="replace")
        try: pkt = Packet.parse(text)
        except (ValueError, IndexError, AttributeError) as e:
            return log.debug("unparseable from %s: %s", addr, e)
        log.debug("<< %s:%d\n%s", addr[0], addr[1], text)
        sl = pkt.status_line
        if sl.startswith("SIP/2.0") and pkt.get("Call-ID") == self.reg_call_id:
            return self.reg_queue.put(pkt)
        if sl.startswith("SIP/2.0") and self.provider and self.provider.is_reg_response(pkt):
            return self.provider.reg_queue.put(pkt)
        name = "on_response" if sl.startswith("SIP/2.0") else self.REQUEST_HANDLERS.get(sl.split(" ", 1)[0])
        if not name: return
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

    def on_response(self, pkt, addr):
        cseq = pkt.get("CSeq") or ""
        cid = pkt.get("Call-ID") or ""
        code = int(pkt.status_line.split()[1])
        log.info("response %s (CSeq %s, Call-ID %s)", pkt.status_line, cseq, cid)
        out = self.out_calls.get(cid)
        if out and "INVITE" in cseq:
            if code in (180, 183):
                log.info("outbound call %s ringing", out["num"])
            elif 200 <= code < 300:
                self.out_answered(out, pkt, addr)
            elif code >= 300:
                log.warning("outbound call to %s failed: %s", out["num"], pkt.status_line)
                self.out_calls.pop(cid, None)
            return
        call = self.provider.calls.get(cid) if self.provider else None
        if call and "INVITE" in cseq:                   # provider-leg INVITE reply
            result = self.provider.handle_response(call, pkt)
            if result == "answered":
                self.provider_answered(call, pkt)
            elif result == "fail":
                self.send(self.in_dialog_response(call.invite, "486 Busy Here",
                                                  extra_to_tag=call.to_tag), call.addr)
                call.stop()
                self.calls.pop(call.id, None)
                self.provider.calls.pop(cid, None)

    def run(self):
        threading.Thread(target=self.listener, daemon=True).start()
        threading.Thread(target=self.registration_loop, daemon=True).start()
        threading.Thread(target=self.heartbeat_loop, daemon=True).start()
        if self.o.call:
            threading.Thread(target=self.call_out, args=(self.o.call,), daemon=True).start()
        if self.local_mode:
            missing = [t for t, need in (("aplay", self.o.local_audio), ("arecord", self.o.local_mic),
                                       ("ffplay", self.o.video_player)) if need and not shutil.which(t)]
            if missing:
                log.warning("audio tools missing (%s) — install alsa-utils / ffmpeg, "
                            "or run --list-audio to see devices", ", ".join(missing))
            log.info("indoor station sim on %s:%d -> registrar %s:%d  [LOCAL headset mode: "
                     "spk=%s mic=%s tone=%s video=%s dev=%s]",
                     self.o.local_ip, self.bind_port, self.server[0], self.server[1],
                     bool(self.o.local_audio), bool(self.o.local_mic), self.o.local_tone,
                     bool(self.o.video_player), self.o.alsa_device)
        else:
            if self.provider.needs_register():
                threading.Thread(target=self.provider.register_loop, daemon=True).start()
            else:
                log.info("no --sip-user/--sip-pass: trunk mode, provider REGISTER skipped")
            log.info("indoor station sim on %s:%d -> registrar %s:%d, bridge via %s:%d to %s",
                     self.o.local_ip, self.bind_port, self.server[0], self.server[1],
                     self.provider.proxy[0], self.provider.proxy[1], self.o.sip_target)
        def terminate(signum, frame): raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, terminate)   # plain `kill` must unregister too
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def shutdown(self):
        """clean exit: end every call (BYE/CANCEL so the registrar's busy
        tracking clears), then Expires=0 to remove our dev_reg row —
        leaving stale state marks the whole system busy (486 to the door)"""
        self.running = False
        for cid in list(self.out_calls):
            try: self.bye_outbound(cid)
            except OSError: pass
        for call in list(self.calls.values()):
            self._end_call_on_shutdown(call)
        self.calls.clear()
        try:
            self.send(self.build_register(expires=0), self.server)
            if self.provider and self.provider.needs_register(): self.provider.unregister()
            time.sleep(0.3)
        except OSError: pass
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
        except OSError: pass
        call.stop()

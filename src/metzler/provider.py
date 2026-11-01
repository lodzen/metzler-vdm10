"""Standard-SIP trunk to a hosted provider (sip.linphone.org) or a PBX (Asterisk)."""

import logging
import queue
import re
import socket
import threading
import time

from metzler.media.intercom import SipIntercomMedia
from metzler.media.srtp import SrtpSession
from metzler.sip.digest import digest_response
from metzler.sip.message import UA, Packet, branch, call_id, tag, uri_of

log = logging.getLogger("indoor")


class SipProvider:
    """Standard-SIP trunk to a hosted provider (e.g. sip.linphone.org).

    Everything the public-SIP side needs lives here so the station only ever
    delegates: REGISTER (digest auth, NAT learn via the echoed Via), one
    outbound INVITE leg per bridged Call, and ACK/CANCEL/BYE for that leg.

    Packet flow:
        station listener  ->  is_reg_response()  -> reg_queue (REGISTER replies)
                          ->  calls[Call-ID]     -> handle_response() (INVITE replies)
        on_invite bridge  ->  invite(call)   (offer built by offer_sdp())
        provider 200 OK   ->  station.provider_answered() -> ack()
        teardown          ->  cancel() / bye()

    The provider object sends on the station's UDP socket (shared source port)
    via the `st` back-reference; it owns no sockets of its own."""

    def __init__(self, st, o):
        self.st, self.o = st, o
        self.proxy = (o.sip_host, o.sip_port)
        self.reg_call_id = call_id()
        self.reg_cseq = 1
        self.reg_queue = queue.Queue()
        self.registered = threading.Event()
        self.public_ip = None           # Via received= learned from the proxy
        self.public_port = None         # Via rport= (our NAT'ed source port)
        self.calls = {}                 # provider-leg Call-ID -> Call
        self.own_ids = set()            # every Call-ID we originated (loop guard)
        try: self.proxy_ip = socket.gethostbyname(o.sip_host)
        except OSError: self.proxy_ip = None
        # SIP identity: the account name, or - for a credential-less PBX trunk
        # (e.g. Asterisk with IP auth) - the station's own extension number
        self.user = o.sip_user or o.username

    def needs_register(self):
        """hosted providers need REGISTER; an IP-authenticated PBX trunk does not"""
        return bool(self.o.sip_user and self.o.sip_pass)

    # -- identity -------------------------------------------------------------

    def sdp_ip(self):
        """address we advertise in provider-leg SDP/Contact (public if known)"""
        return self.public_ip or self.o.local_ip

    def sdp_port(self):
        return self.public_port or self.st.bind_port

    def _base(self, method, uri, frm, br=None):
        p = Packet()
        p.status_line = f"{method} {uri} SIP/2.0"
        p.set("Via", f"SIP/2.0/UDP {self.o.local_ip}:{self.st.bind_port};"
                     f"rport;branch={br or branch()}")
        p.set("Max-Forwards", "70")
        p.set("From", frm)
        p.set("User-Agent", UA)
        return p

    def owns(self, call_id):
        """did we ever originate this Call-ID? (forked-call loop guard - kept
        after the call ends, late fork retransmits must not look like new calls)"""
        return call_id in self.own_ids

    def is_provider_addr(self, addr):
        """request came from the provider proxy/PBX (not from an intercom
        station); ip+port, so a PBX sharing a host with other SIP gear works"""
        return (addr[0], addr[1]) == (self.proxy_ip, self.proxy[1])

    def is_reg_response(self, pkt):
        return pkt.get("Call-ID") == self.reg_call_id

    # -- REGISTER -------------------------------------------------------------

    def _register_pkt(self, auth=None):
        p = self._base("REGISTER", f"sip:{self.o.sip_domain}",
                       f"<sip:{self.o.sip_user}@{self.o.sip_domain}>;tag={self.st.local_tag}")
        p.set("To", f"<sip:{self.o.sip_user}@{self.o.sip_domain}>")
        p.set("Contact", f"<sip:{self.o.sip_user}@{self.o.local_ip}:{self.st.bind_port}>")
        p.set("Call-ID", self.reg_call_id)
        p.set("CSeq", f"{self.reg_cseq} REGISTER")
        p.set("Expires", "600")
        if auth: p.set("Authorization", auth)
        return p

    def register_loop(self):
        """keeps the account registered: REGISTER -> 401 digest retry -> 200.
        Every response's Via carries received=/rport= = our public identity."""
        while self.st.running:
            try:
                self._register_once()
            except (RuntimeError, OSError) as e:
                log.warning("provider register failed: %s", e)
                self.registered.clear()
            time.sleep(240)

    def _register_once(self):
        """one REGISTER round: unauthed -> <=2 digest retries -> 200."""
        while True:                                     # drain stale replies
            try: self.reg_queue.get_nowait()
            except queue.Empty: break
        self.st.send(self._register_pkt(), self.proxy)
        for attempt in range(3):
            resp = self._wait_reply(time.time() + 10)
            if resp is None:
                return log.warning("provider register timed out (next retry in 30s)")
            self._learn_public(resp)
            if resp.status_line.startswith("SIP/2.0 2"):
                return self._mark_registered()
            if resp.status_line.startswith(("SIP/2.0 401", "SIP/2.0 407")):
                if attempt == 2:
                    return log.warning("provider auth failed (check sip password)")
                self.reg_cseq += 1
                self.st.send(self._register_pkt(self._reg_auth(resp)), self.proxy)
            elif re.match(r"SIP/2\.0 [456]", resp.status_line):
                return log.warning("provider register rejected: %s", resp.status_line)

    def _wait_reply(self, deadline):
        try: return self.reg_queue.get(timeout=max(0.1, deadline - time.time()))
        except queue.Empty: return None

    def _reg_auth(self, resp):
        hdr = resp.get("WWW-Authenticate") or resp.get("Proxy-Authenticate")
        return digest_response(hdr, "REGISTER", f"sip:{self.o.sip_domain}",
                               self.o.sip_user, self.o.sip_pass)

    def _mark_registered(self):
        if not self.registered.is_set():
            log.info("provider REGISTERED at %s:%d as %s@%s (public %s:%s)",
                     *self.proxy, self.o.sip_user, self.o.sip_domain,
                     self.public_ip, self.public_port)
        self.registered.set()

    def unregister(self):
        try:
            p = self._register_pkt(); p.set("Expires", "0", replace=True)
            self.st.send(p, self.proxy)
        except OSError: pass

    def _learn_public(self, pkt):
        """NAT detection without STUN: the proxy echoes our Via back with
        received=<ip>/rport=<port> - that is our public identity for SDP."""
        via = pkt.get("Via") or ""
        m = re.search(r"received=([0-9.]+)", via)
        if m: self.public_ip = m.group(1)
        m = re.search(r"rport=(\d+)", via)
        if m: self.public_port = int(m.group(1))

    # -- outbound INVITE leg --------------------------------------------------

    def offer_sdp(self, call):
        """provider leg offer: PCMU audio (intercom calls) or audio + H264
        video (door calls). With --sip-srtp each m= line is RTP/SAVP carrying
        its own SDES key; the media objects keep the matching SrtpSession."""
        streams = ([("audio", call.media)] if call.kind == "intercom"
                   else [("audio", call.audio), ("video", call.video)])
        ip = self.sdp_ip()
        body = f"v=0\r\no=- 0 0 IN IP4 {ip}\r\ns=Talk session\r\nc=IN IP4 {ip}\r\nt=0 0\r\n"
        for kind, media in streams:
            media.srtp = SrtpSession() if self.o.sip_srtp and not call.prov_plain else None
            body += self._media_block(kind, media)
        return body

    @staticmethod
    def _media_block(kind, media):
        port = media.rtp_port if isinstance(media, SipIntercomMedia) else media.port_b
        proto = "RTP/SAVP" if media.srtp else "RTP/AVP"
        crypto = media.srtp.crypto_attr() if media.srtp else ""
        if kind == "audio":
            return (f"m=audio {port} {proto} 0 101\r\n{crypto}"
                    "a=rtpmap:0 PCMU/8000\r\na=rtpmap:101 telephone-event/8000\r\n"
                    "a=fmtp:101 0-16\r\na=sendrecv\r\n")
        return (f"m=video {port} {proto} 96\r\n{crypto}a=rtpmap:96 H264/90000\r\n"
                "a=fmtp:96 packetization-mode=1;profile-level-id=4D001E\r\na=sendrecv\r\n")

    @staticmethod
    def bind_srtp(media, answer_media):
        """take the peer's key from its answered m= line; a plain RTP/AVP
        answer drops SRTP for that stream. Returns 'srtp'|'rtp' for logging."""
        if media.srtp and answer_media and media.srtp.accept_answer(answer_media):
            return "srtp"
        media.srtp = None
        return "rtp"

    def invite(self, call):
        """send the outbound INVITE for a bridged call; registers the leg so
        listener() can route its replies via calls[prov_call_id]"""
        self.calls[call.prov_call_id] = call
        self.own_ids.add(call.prov_call_id)
        self.st.send(self._invite_pkt(call), self.proxy)
        call.state = "bridging"

    def _invite_pkt(self, call, auth=None, auth_name="Authorization"):
        uri = self.o.sip_target
        call.prov_branch = branch()
        call.prov_cseq += 1
        p = self._base("INVITE", uri,
                       f'"{self.o.name}" <sip:{self.user}@{self.o.sip_domain}>;tag={tag()}',
                       call.prov_branch)
        p.set("Contact", f"<sip:{self.user}@{self.sdp_ip()}:{self.sdp_port()}>")
        p.set("To", f"<{uri}>")
        p.set("Call-ID", call.prov_call_id)
        p.set("CSeq", f"{call.prov_cseq} INVITE")
        p.set("Allow", "INVITE, ACK, CANCEL, BYE, REFER, INFO, OPTIONS, MESSAGE")
        p.set("Content-Type", "application/sdp")
        if auth: p.set(auth_name, auth)
        p.body = self.offer_sdp(call)
        call.prov_from = p.get("From")
        call.prov_uri = uri
        return p

    def _ack_failure(self, call, resp):
        """a non-2xx final response to INVITE must be ACK'ed (same branch,
        same CSeq number) or the proxy retransmits it ~7 times"""
        p = self._base("ACK", call.prov_uri or self.o.sip_target,
                       resp.get("From"), call.prov_branch)
        p.set("To", resp.get("To"))
        p.set("Call-ID", call.prov_call_id)
        p.set("CSeq", f"{call.prov_cseq} ACK")
        self.st.send(p, self.proxy)

    def handle_response(self, call, resp):
        """dispatch one response on the provider INVITE leg"""
        code = int(resp.status_line.split()[1])
        self._learn_public(resp)
        if code in (401, 407):                          # digest challenge
            self._ack_failure(call, resp)               # stop the retransmits
            call.prov_auths += 1
            if call.prov_auths > 2:
                log.warning("provider INVITE auth failed - busy to indoor caller")
                return "fail"
            # 407 needs Proxy-Authorization (header + challenge); 401 uses Authorization
            hdr = resp.get("Proxy-Authenticate" if code == 407 else "WWW-Authenticate")
            auth = digest_response(hdr, "INVITE", call.prov_uri,
                                   self.o.sip_user, self.o.sip_pass)
            name = "Proxy-Authorization" if code == 407 else "Authorization"
            self.st.send(self._invite_pkt(call, auth, name), self.proxy)
            return "auth"
        if code < 200: return "ringing"                 # 100/110/180/183 - provisional
        if 200 <= code < 300:  return "answered"
        self._ack_failure(call, resp)                   # >=300 final: ACK to stop retransmits
        if code == 488 and self.o.sip_srtp and not call.prov_plain:
            # peer without SRTP support (e.g. a stock Asterisk) - re-offer plain RTP once
            log.info("provider refused the SRTP offer (488) - retrying with plain RTP")
            call.prov_plain = True
            self.st.send(self._invite_pkt(call), self.proxy)
            return "auth"
        return "fail"

    def ack(self, call, resp):
        """ACK the provider 200 OK; To/From echo the response, target = its Contact"""
        to = resp.get("To")
        contact = resp.get("Contact") or f"<{self.o.sip_target}>"
        uri = uri_of(contact)
        call.prov_to, call.prov_uri = to, uri
        p = self._base("ACK", uri, resp.get("From"))
        p.set("Contact", self.st.contact())
        p.set("To", to)
        p.set("Call-ID", call.prov_call_id)
        p.set("CSeq", "1 ACK")
        self.st.send(p, self.proxy)

    # -- leg teardown ---------------------------------------------------------

    def _leg_pkt(self, call, method, cseq):
        uri = call.prov_uri or self.o.sip_target
        p = self._base(method, uri, call.prov_from)
        p.set("To", call.prov_to if method == "BYE" and call.prov_to else f"<{uri}>")
        p.set("Call-ID", call.prov_call_id)
        p.set("CSeq", cseq)
        return p

    def cancel(self, call):
        try: self.st.send(self._leg_pkt(call, "CANCEL", "1 CANCEL"), self.proxy)
        except OSError: pass

    def bye(self, call):
        try: self.st.send(self._leg_pkt(call, "BYE", "2 BYE"), self.proxy)
        except OSError: pass

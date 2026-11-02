"""Outbound calls to other stations (--call, mimics _client_call)."""

import logging
import time

from metzler.media.intercom import IntercomMedia
from metzler.media.local import LocalMediaBridge
from metzler.sip.message import UA, Packet, branch, call_id, uri_of
from metzler.sip.sdp import media_addr, parse_sdp, sdp_answer_talkback

log = logging.getLogger("indoor")


class OutboundCallMixin:
    """Outbound calls to other stations (--call, mimics _client_call)."""

    def call_out(self, num, bridged=None):
        """place a call to devLongNum `num` via the registrar (request-uri :5065).
        `bridged` links an inbound provider Call: its answer/ring/fail events
        get forwarded to the app instead of driving local headset media."""
        if not self.registered.wait(15):
            log.error("not registered, cannot call")
            return False
        # --call accepts "num" (via registrar) or "num@ip" (direct :5060 —
        # the firmware's netAltRoute path in sip_callLongNum, sip_client.c)
        peer_ip = None
        if "@" in num:
            num, peer_ip = num.split("@", 1)
        host, port = (peer_ip, 5060) if peer_ip else (self.o.server, self.o.server_port)
        uri = f"sip:{num}@{host}:{port}"
        p = self.base_request("INVITE", uri,
                              to_uri=f"<sip:{num}@{host}:{port}>",
                              frm_uri=f"<sip:{self.o.username}@{self.o.local_ip}>;tag={self.local_tag}")
        p.set("Call-ID", call_id()); p.set("CSeq", "1 INVITE")
        p.set("Subject", "This is a call to extension.")
        p.set("Content-Type", "application/sdp")
        # indoor->indoor offer == the header-only answer template (no m= lines):
        # media flows via the symmetric rtsp://<peer>:554 pulls opened after ACK.
        p.body = sdp_answer_talkback(self.o.serial, self.o.local_ip, opts=self.o)
        cid = p.get("Call-ID")
        self.out_calls[cid] = {"num": num, "invite": p, "local": None,
                               "to_tag": None, "peer": self.server,
                               "bridged": bridged, "ts": time.time()}
        if bridged: bridged.out_cid = cid
        log.info("calling %s via %s:%d ...", num, host, port)
        self.out_calls[cid]["peer"] = (host, port)
        self.send(p, (host, port))
        return True

    def out_answered(self, out, resp, addr):
        resp_sdp = parse_sdp(resp.body)
        self._ack_outbound(out, resp)
        out["established"] = True
        if out.get("bridged"):
            return self.inbound_bridged_answered(out, resp_sdp, addr)
        try:
            out["local"] = self._outbound_media(resp_sdp, addr)
            out["local"].start()
        except OSError as e:
            log.warning("media start failed: %s", e)
        log.info("outbound call to %s established (peer %s)",
                 out["num"], resp_sdp["ip"] or addr[0])

    def _ack_outbound(self, out, resp):
        """ACK the callee's 200 OK at its Contact; remember the dialog for BYE"""
        out["to_tag"] = resp.get("To") or ""
        out["ack_uri"] = uri_of(resp.get("Contact") or out["invite"].status_line.split()[1])
        ack = Packet()
        ack.status_line = f"ACK {out['ack_uri']} SIP/2.0"
        ack.set("Via", f"SIP/2.0/UDP {self.o.local_ip}:{self.bind_port};rport;branch={branch()}")
        ack.set("Max-Forwards", "70")
        ack.set("Contact", self.contact())
        ack.set("To", out["to_tag"]); ack.set("From", resp.get("From"))
        ack.set("Call-ID", resp.get("Call-ID")); ack.set("CSeq", "1 ACK")
        ack.set("User-Agent", UA)
        self.send(ack, out["peer"])

    def _outbound_media(self, resp_sdp, addr):
        """door-style answer with media ports -> RTP to the headset; an
        intercom (header-only) answer -> symmetric rtsp://<peer>:554 pull"""
        a, v = media_addr(resp_sdp, "audio"), media_addr(resp_sdp, "video")
        if a or v:
            return LocalMediaBridge(self.o, a, v)
        return IntercomMedia(self.o, self.rtsp, resp_sdp["ip"] or addr[0])

    def bye_outbound(self, cid):
        out = self.out_calls.pop(cid, None)
        if not out: return
        if out.get("local"): out["local"].stop()
        if not out.get("established"):      # early dialog -> CANCEL
            p = Packet()
            uri = out["invite"].status_line.split()[1]
            p.status_line = f"CANCEL {uri} SIP/2.0"
            # RFC 3261 9.1: the CANCEL only matches the INVITE transaction
            # with the same Via branch and CSeq number (else a 481 and the
            # callee keeps ringing)
            p.set("Via", out["invite"].get("Via"))
            p.set("Max-Forwards", "70")
            p.set("To", out["invite"].get("To")); p.set("From", out["invite"].get("From"))
            p.set("Call-ID", cid)
            p.set("CSeq", out["invite"].get("CSeq").replace("INVITE", "CANCEL"))
            p.set("User-Agent", UA)
            try: self.send(p, out["peer"])
            except OSError: pass
            return
        p = Packet()
        uri = out.get("ack_uri") or out["invite"].status_line.split()[1]
        p.status_line = f"BYE {uri} SIP/2.0"
        p.set("Via", f"SIP/2.0/UDP {self.o.local_ip}:{self.bind_port};rport;branch={branch()}")
        p.set("Max-Forwards", "70")
        p.set("Contact", self.contact())
        p.set("To", out["to_tag"]); p.set("From", out["invite"].get("From"))
        p.set("Call-ID", cid); p.set("CSeq", "2 BYE"); p.set("User-Agent", UA)
        try: self.send(p, out["peer"])
        except OSError: pass

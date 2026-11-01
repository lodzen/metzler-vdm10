"""Call teardown: CANCEL/BYE from either leg."""

import logging

from metzler.sip.message import UA, Packet, branch, uri_of

log = logging.getLogger("indoor")


class TeardownMixin:
    """Call teardown: CANCEL/BYE from either leg."""

    def _end_provider_leg(self, call):
        """intercom/door caller went away -> drop the provider leg too"""
        if self.provider:
            if call.state == "established": self.provider.bye(call)
            else: self.provider.cancel(call)
            self.provider.calls.pop(call.prov_call_id, None)

    def on_cancel(self, pkt, addr):
        self.send(self.in_dialog_response(pkt, "200 OK"), addr)
        cid = pkt.get("Call-ID")
        call = self.calls.get(cid)
        if call:
            term = self.in_dialog_response(call.invite, "487 Request Terminated",
                                           extra_to_tag=call.to_tag)
            self.send(term, addr)
            self._end_provider_leg(call)
            call.stop(); self.calls.pop(cid, None)

    def on_bye(self, pkt, addr):
        self.send(self.in_dialog_response(pkt, "200 OK"), addr)
        cid = pkt.get("Call-ID")
        if cid in self.calls:                       # intercom/door caller hung up
            call = self.calls.pop(cid)
            self._end_provider_leg(call)
            call.stop()
            return
        if self.provider and cid in self.provider.calls:   # SIP peer hung up
            call = self.provider.calls.pop(cid)
            self.bye_intercom(call)                 # end the intercom leg too
            call.stop()
            self.calls.pop(call.id, None)
            return
        elif cid in self.out_calls:
            out = self.out_calls.pop(cid)
            out["local"].stop()
            log.info("outbound call to %s ended by peer", out["num"])

    def bye_intercom(self, call):
        """the SIP peer hung up - BYE the intercom/door caller on its dialog"""
        p = Packet()
        uri = uri_of(call.invite.get("Contact") or call.invite.status_line.split()[1])
        p.status_line = f"BYE {uri} SIP/2.0"
        p.set("Via", f"SIP/2.0/UDP {self.o.local_ip}:{self.bind_port};rport;branch={branch()}")
        p.set("Max-Forwards", "70")
        p.set("Contact", self.contact())
        p.set("To", call.invite.get("From"))
        frm = call.invite.get("To") or ""
        p.set("From", f"{frm};tag={call.to_tag}" if "tag=" not in frm else frm)
        p.set("Call-ID", call.id); p.set("CSeq", "1 BYE")
        p.set("User-Agent", UA)
        try: self.send(p, call.addr)
        except OSError: pass

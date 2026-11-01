"""Call teardown: CANCEL/BYE from either leg."""

import logging

from metzler.sip.message import UA, Packet, branch, uri_of

log = logging.getLogger("indoor")


class TeardownMixin:
    """Call teardown: CANCEL/BYE from either leg."""

    def _end_provider_leg(self, call):
        """one side went away -> drop the other leg too"""
        if call.inbound:                        # app leg ended -> end our intercom call
            return self.bye_outbound(call.out_cid)
        if call.fritz_leg:                      # door-call race still open? CANCEL it
            self.fritz.cancel_leg(call)
            call.fritz_leg = None
        trunk = call.trunk or self.provider     # the live/winning SIP leg
        if trunk and call.prov_call_id in trunk.calls:
            if call.state == "established": trunk.bye(call)
            else: trunk.cancel(call)
            trunk.calls.pop(call.prov_call_id, None)

    def on_cancel(self, pkt, addr):
        self.send(self.in_dialog_response(pkt, "200 OK"), addr)
        cid = pkt.get("Call-ID")
        call = self.calls.get(cid)
        if call:
            self.reply(call, self.in_dialog_response(call.invite, "487 Request Terminated",
                                                     extra_to_tag=call.to_tag))
            self._end_provider_leg(call)
            self._close_call(call, cid)

    def on_bye(self, pkt, addr):
        self.send(self.in_dialog_response(pkt, "200 OK"), addr)
        cid = pkt.get("Call-ID")
        if cid in self.calls:                       # intercom/door caller hung up
            call = self.calls[cid]
            self._end_provider_leg(call)
            self._close_call(call, cid)
            return
        for trunk in (self.provider, self.fritz):
            if trunk and cid in trunk.calls:        # a SIP leg's peer hung up
                call = trunk.calls.pop(cid)
                if call.fritz_leg:                  # don't leave the fritzfon ringing
                    self.fritz.cancel_leg(call)
                    call.fritz_leg = None
                self.bye_intercom(call)             # end the intercom leg too
                self._close_call(call, call.id)
                return
        if cid in self.out_calls:
            out = self.out_calls.pop(cid)
            if out.get("local"): out["local"].stop()
            if out.get("bridged"):              # intercom hung up -> BYE the app
                call = out["bridged"]
                self.bye_intercom(call)
                self._close_call(call, call.id)
            log.info("outbound call to %s ended by peer", out["num"])

    def bye_intercom(self, call):
        """BYE whoever INVITEd us on that dialog (intercom/door caller, or the
        SIP app on an inbound-bridged call)"""
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

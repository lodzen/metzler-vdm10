"""Registration at the intercom registrar (REGISTER with the
<regXML> body, Digest retry) and the MESSAGE heartbeat."""

import logging
import queue
import re
import threading
import time

from metzler.sip.digest import digest_response
from metzler.sip.message import call_id

log = logging.getLogger("indoor")


class IntercomRegistration:
    """the station's REGISTER session at the intercom registrar (regXML +
    digest retry). Replies are routed by reg Call-ID in handle_datagram.

    Note: the registrar's dev_reg table keys devices by serial_no OR
    dev_ip - two extensions from one source IP can never coexist, so
    there is only ever one registration per station."""

    def __init__(self, st, o):
        self.st, self.o = st, o
        self.username, self.name = o.username, o.name
        self.password = o.password
        self.mac, self.serial = o.mac, o.serial
        self.reg_call_id, self.reg_cseq = call_id(), 1
        self.reg_queue = queue.Queue()
        self.registered = threading.Event()

    def is_reg_response(self, pkt):
        return pkt.get("Call-ID") == self.reg_call_id

    def reg_xml(self):
        # exact firmware field order (_sip_xml_get_reg_info_xml): serial, mac, name, floorNum
        return (
            f"<regXML>\r\n<regDevSerial>{self.serial}</regDevSerial>\r\n"
            f"<regDevMacAddr>{self.mac}</regDevMacAddr>\r\n"
            f"<regDevName>{self.name}</regDevName>\r\n"
            f"<floorNum>{self.o.floor_num}</floorNum>\r\n</regXML>"
        )

    def build_register(self, expires=None, auth=None):
        st = self.st
        uri = f"sip:{self.o.server}:{self.o.server_port}"
        # firmware: From = sip:<devLongNum>@<localip>, To = sip:<devLongNum>@<registrar>
        p = st.base_request(
            "REGISTER",
            uri,
            to_uri=f"<sip:{self.username}@{self.o.server}:{self.o.server_port}>",
            frm_uri=f'"{self.name}"<sip:{self.username}@{self.o.local_ip}>;tag={st.local_tag}',
        )
        p.set("Call-ID", self.reg_call_id)
        p.set("CSeq", f"{self.reg_cseq} REGISTER")
        expires = self.o.expires if expires is None else expires
        p.set("Expires", str(expires))
        p.set("Content-Type", "text/xml")
        if auth:
            p.set("Authorization", auth)
        p.body = self.reg_xml() if expires else ""  # unregister (Expires: 0) has no regXML
        return p

    def register_loop(self):
        while self.st.running:
            try:
                self.register_once()
                self.registered.set()
            except (RuntimeError, OSError) as e:
                log.warning("register failed: %s", e)
                self.registered.clear()
            time.sleep(max(30, self.o.expires // 2))

    def register_once(self):
        while True:  # drain stale responses
            try:
                self.reg_queue.get_nowait()
            except queue.Empty:
                break
        p = self.build_register()
        self.st.send(p, self.st.server)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                resp = self.reg_queue.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                break
            if resp.status_line.startswith(("SIP/2.0 401", "SIP/2.0 407")):
                hdr = resp.get("WWW-Authenticate") or resp.get("Proxy-Authenticate")
                self.reg_cseq += 1
                uri = f"sip:{self.o.server}:{self.o.server_port}"
                auth = digest_response(hdr, "REGISTER", uri, self.username, self.password)
                self.st.send(self.build_register(auth=auth), self.st.server)
            elif resp.status_line.startswith("SIP/2.0 2"):
                log.info(
                    "REGISTERED at %s:%d as %s (expires %ds)",
                    self.st.server[0],
                    self.st.server[1],
                    self.username,
                    self.o.expires,
                )
                return
            elif re.match(r"SIP/2\.0 [456]", resp.status_line):
                raise RuntimeError(resp.status_line)
        raise RuntimeError("register timeout")

    def unregister(self):
        self.st.send(self.build_register(expires=0), self.st.server)


class RegistrationMixin:
    """registration plumbing + the MESSAGE heartbeat; IntercomRegistration
    instance is created by init_registrations()"""

    def init_registrations(self):
        self.regs = [IntercomRegistration(self, self.o)]
        self.registered = self.regs[0].registered  # gates outbound calls

    def reg_xml(self):
        return self.regs[0].reg_xml()

    def build_register(self, expires=None, auth=None):
        return self.regs[0].build_register(expires, auth)

    def heartbeat_loop(self):
        while not self.registered.is_set() and self.running:
            time.sleep(2)
        while self.running:
            target = self.o.heartbeat_target or self.o.username
            uri = f"sip:{target}@{self.o.server}:{self.o.server_port}"
            p = self.base_request("MESSAGE", uri)
            p.set("Call-ID", call_id())
            p.set("CSeq", "1 MESSAGE")
            p.set("Content-Type", "text/xml")
            # firmware format: <header>{method,action,from-ip,to-ip}</header><body>{num}
            p.body = (
                "<videoIntercomMsg>\r\n<header>\r\n<method>1</method>\r\n"
                "<action>1</action>\r\n"
                f"<from>{self.o.local_ip}</from>\r\n<to>{self.o.server}</to>\r\n"
                "</header>\r\n<body>\r\n<num>1</num>\r\n</body>\r\n</videoIntercomMsg>"
            )
            try:
                self.send(p, self.server)
            except OSError:
                pass
            time.sleep(self.o.heartbeat_interval)

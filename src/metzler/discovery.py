"""LAN discovery for VDM10 devices (stdlib only, no dependencies).

Two mechanisms, both verified against live firmware V2.0.24:

1. SADP inquiry (Hikvision Secure Active Discovery Protocol): an XML
   ``<Probe>`` to the multicast group ``239.255.255.250:37020`` (plus optional
   unicast sweep - multicast may be filtered). Outdoor stations answer with a
   ``<ProbeMatch>`` XML (DeviceType/DeviceDescription/DeviceSN/MAC/IPv4).
   The indoor units run the SADP *client* side (``sadpc_*``,
   ``confrelatedev_check_doorDev``) to find doors but do not answer inquiries.

2. Registrar fingerprint: a kind-1 indoor REGISTER to ``host:5065``. The main
   indoor's registrar replies to ``source-ip:5060`` - ``htons(5060)`` is
   hard-coded in ``send_reply`` (``vdm10_1.0/sip/server/registrar.c``), NOT
   the Via port - so the probing socket must bind :5060. Any SIP reply means
   a registrar is present (main indoor); extension indoors have no registrar
   and stay silent. Status codes map extension-slot state: ``410 Sip Gone``
   = number taken by another device, ``200 OK`` = slot free - but a ``200``
   *inserts* a dev_reg row, so every probe is followed by an ``Expires: 0``
   unregister to clean up (verified: 200 OK).
"""

from __future__ import annotations

import socket
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

SADP_GROUP = "239.255.255.250"
SADP_PORT = 37020
REGISTRAR_PORT = 5065
SIP_UA_PORT = 5060
VDM10_OUTDOOR_TYPE = "57619"
VDM10_MAC_PREFIXES = ("bc:9b:5e",)
EXT_BASE = "1000000000"  # devLongNum prefix; + slot digit = 11-char AOR
EXT_SLOTS = range(0, 6)  # firmware caps registered extensions at idx 5

_SADP_PROBE = '<?xml version="1.0" encoding="utf-8"?><Probe><Uuid>{u}</Uuid><Types>inquiry</Types></Probe>'


def _parse_probe_match(data: bytes) -> dict | None:
    try:
        root = ET.fromstring(data.decode("utf-8", "replace"))
    except ET.ParseError:
        return None
    if root.tag != "ProbeMatch":
        return None
    return {c.tag: (c.text or "") for c in root}


def sadp_probe(targets=(), timeout=3.0, multicast=True, iface_ip=None):
    """SADP inquiry -> list of {tag: value} dicts keyed by responding IP.

    ``targets``: extra unicast (host, port) tuples for the sweep fallback.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    if iface_ip:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(iface_ip))
    s.settimeout(timeout)
    s.bind(("", 0))
    probe = _SADP_PROBE.format(u=uuid.uuid4()).encode()
    dests = ([(SADP_GROUP, SADP_PORT)] if multicast else []) + [(h, p) for h, p in targets]
    for d in dests:
        try:
            s.sendto(probe, d)
        except OSError:
            continue
    out, seen = {}, set()
    end = time.time() + timeout
    while time.time() < end:
        try:
            data, addr = s.recvfrom(65535)
        except TimeoutError:
            break
        if addr[0] in seen:
            continue
        m = _parse_probe_match(data)
        if m is None:
            continue
        seen.add(addr[0])
        m["_ip"] = addr[0]
        out[addr[0]] = m
    s.close()
    return list(out.values())


def is_vdm10_outdoor(match: dict) -> bool:
    mac = (match.get("MAC") or "").lower()
    return match.get("DeviceType") == VDM10_OUTDOOR_TYPE or mac.startswith(VDM10_MAC_PREFIXES)


def _reg_packet(host, ext, call_id, expires, serial, mac, floor, local_ip, listen_port, reg_port):
    body = ""
    if expires:
        body = (
            "<regXML>\r\n"
            f"<regDevSerial>{serial}</regDevSerial>\r\n"
            f"<regDevMacAddr>{mac}</regDevMacAddr>\r\n"
            f"<sipNum>{ext}</sipNum>\r\n"
            f"<floorNum>{floor}</floorNum>\r\n"
            "</regXML>"
        )
    via = f"SIP/2.0/UDP {local_ip}:{listen_port};branch=z9hG4bK-{call_id[:16]}"
    return (
        f"REGISTER sip:{host}:{reg_port} SIP/2.0\r\n"
        f"Via: {via}\r\n"
        f"To: <sip:{ext}@{host}:{reg_port}>\r\n"
        f'From: "probe"<sip:{ext}@{local_ip}>;tag=p{call_id[:8]}\r\n'
        f"Call-ID: {call_id}\r\nCSeq: 1 REGISTER\r\n"
        f"Contact: <sip:{ext}@{local_ip}:{listen_port}>\r\n"
        f"Expires: {expires}\r\nMax-Forwards: 70\r\n"
        f"User-Agent: eXosip/3.6.0\r\n"
        f"Content-Type: text/xml\r\nContent-Length: {len(body)}\r\n\r\n{body}"
    ).encode()


def _status_of(data: bytes) -> int | None:
    try:
        line = data.split(b"\r\n", 1)[0].decode("ascii", "replace")
        if line.startswith("SIP/2.0 "):
            return int(line.split()[1])
    except (IndexError, ValueError):
        pass
    return None


def registrar_probe(
    host,
    ext=None,
    timeout=3.0,
    listen_port=SIP_UA_PORT,
    reg_port=REGISTRAR_PORT,
    serial="Q12345678",
    mac="02:00:11:22:33:44",
    floor=3,
    local_ip=None,
):
    """REGISTER fingerprint -> SIP status int, or None when the host is silent.

    The probe binds ``listen_port`` because the VDM10 registrar replies to
    source-ip:5060 unconditionally (``send_reply`` hard-codes the port). On a
    2xx answer the probe *registered* an extension, so an ``Expires: 0``
    unregister is sent immediately to remove the dev_reg row again.
    """
    ext = ext or f"{EXT_BASE}0"
    if local_ip is None:
        local_ip = local_ip_for(host)
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    u.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    u.settimeout(timeout)
    u.bind(("", listen_port))  # registrar answers only here (firmware)
    call_id = f"vdm10-probe-{uuid.uuid4().hex[:12]}"
    u.sendto(
        _reg_packet(host, ext, call_id, 900, serial, mac, floor, local_ip, listen_port, reg_port),
        (host, reg_port),
    )
    status, end = None, time.time() + timeout
    while time.time() < end:
        try:
            data, _ = u.recvfrom(65535)
        except TimeoutError:
            break
        status = _status_of(data) or status
        if status and status >= 200:  # final response (skip 1xx)
            break
    if status and 200 <= status < 300:
        u.sendto(
            _reg_packet(host, ext, f"{call_id}-unreg", 0, serial, mac, floor, local_ip, listen_port, reg_port),
            (host, reg_port),
        )
        try:
            u.recvfrom(65535)  # drain the unregister ack
        except TimeoutError:
            pass
    u.close()
    return status


def scan_extension_slots(host, slots=EXT_SLOTS, **kw):
    """probe ``{base}{slot}`` for each slot -> {ext_number: status|None}.

    Live semantics: 410 = slot taken by a device, 200 = free (slot row is
    removed again by the probe's unregister follow-up), None = no registrar.
    """
    return {f"{EXT_BASE}{slot}": registrar_probe(host, f"{EXT_BASE}{slot}", **kw) for slot in slots}


def free_extension(host, **kw) -> str | None:
    """lowest free (status 200) extension long-number on the registrar."""
    for ext, status in scan_extension_slots(host, **kw).items():
        if status and 200 <= status < 300:
            return ext
    return None


def is_vdm10_host(ip: str) -> bool:
    """OUI check via the ARP table - Metzler/Hikvision VDM10 OUIs."""
    try:
        for line in Path("/proc/net/arp").read_text().splitlines()[1:]:
            cols = line.split()
            if cols[0] == ip:
                return cols[3].lower().startswith(VDM10_MAC_PREFIXES)
    except OSError:
        pass
    return True  # outside Linux / no arp entry -> don't filter it out


def local_ip_for(host, port=REGISTRAR_PORT) -> str:
    """the local IP used to reach ``host`` (UDP connect, no traffic sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, port))
        return s.getsockname()[0]
    finally:
        s.close()


def subnet_of(ip: str) -> list[str]:
    """/24 host list derived from an IP (the VDM10 default LAN layout)."""
    base = ip.rsplit(".", 1)[0]
    return [f"{base}.{i}" for i in range(1, 255)]


def tcp_candidates(subnet, ports=(8000, 8102), connect_timeout=0.5):
    """parallel TCP connect scan -> {ip: [open ports]} - the Hikvision SDK/
    visNet signature narrows REGISTER fingerprinting to a handful of hosts."""
    import concurrent.futures

    def probe(host, port):
        try:
            t = socket.create_connection((host, port), connect_timeout)
            t.close()
            return port
        except OSError:
            return None

    out = {}
    with concurrent.futures.ThreadPoolExecutor(64) as ex:
        for host, port in [(h, p) for h in subnet for p in ports]:
            r = ex.submit(probe, host, port)
            if r.result() is not None:
                out.setdefault(host, []).append(r.result())
    return out


def discover(timeout=3.0, subnet=None, local_ip=None):
    """full LAN sweep -> {"doors": [...], "registrars": [...], "candidates": [...]}

    - ``doors``: SADP ProbeMatch dicts of outdoor stations (DeviceType 57619
      or VDM10 OUI)
    - ``registrars``: hosts that answered the :5065 REGISTER fingerprint -
      i.e. the main indoor (extension indoors stay silent)
    - ``candidates``: hosts with the Hikvision TCP signature that were neither
      SADP responders nor registrars - shown as "possible indoor" fallback
    """
    local_ip = local_ip or _default_local_ip()
    subnet = subnet or (subnet_of(local_ip) if local_ip else [])
    doors = [
        m
        for m in sadp_probe(targets=((h, SADP_PORT) for h in subnet), timeout=timeout, iface_ip=local_ip)
        if is_vdm10_outdoor(m)
    ]
    door_ips = {d["_ip"] for d in doors}
    # cheap pre-filter: only Hikvision-signature hosts get the SIP fingerprint
    candidates = tcp_candidates(subnet)
    registrars = [
        h
        for h in candidates
        if h not in door_ips and registrar_probe(h, timeout=min(timeout, 2.0), local_ip=local_ip) is not None
    ]
    return {
        "doors": doors,
        "registrars": registrars,
        "candidates": [h for h in candidates if h not in door_ips and h not in registrars],
    }


def _default_local_ip():
    for target in ("192.168.1.1", "10.0.0.1", "8.8.8.8"):
        try:
            return local_ip_for(target, 80)
        except OSError:
            continue
    return None

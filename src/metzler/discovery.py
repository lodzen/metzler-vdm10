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

import random
import select
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


def _bind_probe_sock(listen_port):
    """the reply socket probes must use: the VDM10 registrar answers to
    source-ip:5060 unconditionally, so every probe shares this bind. None
    when the port is taken (a running station holds it)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("", listen_port))
    except OSError:
        s.close()
        return None
    s.setblocking(False)
    return s


def _collect_status(sock, deadline, pending, key_of):
    """recv SIP replies until ``deadline`` -> {key: status int}.

    ``key_of(data, addr)`` maps a datagram to a pending key (None = stray
    datagram, e.g. a late unregister-200 for an earlier probe)."""
    found = {}
    while pending and time.monotonic() < deadline:
        ready, _, _ = select.select([sock], [], [], max(0.0, deadline - time.monotonic()))
        if not ready:
            break
        try:
            data, addr = sock.recvfrom(65535)
        except OSError:
            continue
        key = key_of(data, addr)
        if key is None or key not in pending:
            continue
        pending.discard(key)
        if data.startswith(b"SIP/2.0 "):
            try:
                found[key] = int(data.split(None, 2)[1])
            except (IndexError, ValueError):
                pass
    return found


def _unregister(sock, host, packet, reg_port):
    """a 200 inserted a dev_reg row - Expires:0 removes it; the registrar's
    sync hook stalls its SIP loop after an insert, so retry past that
    window (a missed row still dies with its TTL)."""
    for _ in range(3):
        sock.sendto(packet, (host, reg_port))
        if _probe_reply(sock, 1.5):
            break


def scan_extension_slots(host, slots=EXT_SLOTS, **kw):
    """probe ``{base}{slot}`` for each slot -> {ext_number: status|None}.

    Live semantics: 410 = slot taken by a device, 200 = free (slot row is
    removed again by the probe's unregister follow-up), None = no registrar.
    All REGISTERs go out up front on one shared reply socket and the replies
    are matched back by their echoed Call-ID - ``timeout`` bounds the whole
    sweep, so a silent host costs one wait instead of one per slot.
    """
    local_ip = kw.get("local_ip") or local_ip_for(host)
    timeout = float(kw.get("timeout", 3.0))
    serial = kw.get("serial", "Q12345678")
    mac = kw.get("mac", "02:00:11:22:33:44")
    floor = kw.get("floor", 3)
    reg_port = kw.get("reg_port", REGISTRAR_PORT)
    listen_port = kw.get("listen_port", SIP_UA_PORT)
    exts = [f"{EXT_BASE}{slot}" for slot in slots]
    sock = _bind_probe_sock(listen_port)
    if sock is None:
        return dict.fromkeys(exts)
    try:
        packets = {
            ext: _reg_packet(host, ext, f"vdm10-scan-{ext}", 900, serial, mac, floor, local_ip, listen_port, reg_port)
            for ext in exts
        }

        def blast(targets):
            for ext in targets:
                sock.sendto(packets[ext], (host, reg_port))

        def match(data, _addr):
            return next((e for e in pending if f"vdm10-scan-{e}".encode() in data), None)

        pending = set(exts)
        found = {}
        end = time.monotonic() + timeout
        blast(pending)
        found.update(_collect_status(sock, min(end, time.monotonic() + min(timeout, 0.8)), pending, match))
        if pending:
            blast(pending)
            found.update(_collect_status(sock, end, pending, match))
        for ext, code in found.items():
            if code == 200:
                _unregister(
                    sock,
                    host,
                    _reg_packet(
                        host, ext, f"vdm10-scan-{ext}-unreg", 0, serial, mac, floor, local_ip, listen_port, reg_port
                    ),
                    reg_port,
                )
        return {ext: found.get(ext) for ext in exts}
    finally:
        sock.close()


def registrar_probe_many(hosts, timeout=2.0, local_ip=None, **kw):
    """kind-1 indoor REGISTER fingerprint against several hosts at once ->
    the subset that answered (a registrar listens there).

    Per-host probing can't parallelize: the registrar replies to source:5060
    and parallel sockets would steal each other's datagrams. One shared
    socket blasts REGISTERs to all hosts and matches replies by source IP -
    ``timeout`` bounds the whole fingerprint, not each host.
    """
    hosts = list(hosts)
    if not hosts:
        return set()
    timeout = float(timeout)
    serial = kw.get("serial", "Q12345678")
    mac = kw.get("mac", "02:00:11:22:33:44")
    floor = kw.get("floor", 3)
    reg_port = kw.get("reg_port", REGISTRAR_PORT)
    listen_port = kw.get("listen_port", SIP_UA_PORT)
    ext = kw.get("ext") or f"{EXT_BASE}0"
    lip = local_ip or local_ip_for(hosts[0], reg_port)
    sock = _bind_probe_sock(listen_port)
    if sock is None:
        return set()
    try:
        cid = f"vdm10-probe-{uuid.uuid4().hex[:12]}"
        packets = {h: _reg_packet(h, ext, cid, 900, serial, mac, floor, lip, listen_port, reg_port) for h in hosts}
        pending = set(hosts)
        end = time.monotonic() + timeout

        def blast(targets):
            for h in targets:
                sock.sendto(packets[h], (h, reg_port))

        def by_addr(_data, addr):
            return addr[0]

        blast(pending)
        found = _collect_status(sock, min(end, time.monotonic() + min(timeout, 0.8)), pending, by_addr)
        if pending:
            blast(pending)
            found.update(_collect_status(sock, end, pending, by_addr))
        for h, code in found.items():
            if code == 200:
                _unregister(
                    sock,
                    h,
                    _reg_packet(h, ext, f"{cid}-unreg", 0, serial, mac, floor, lip, listen_port, reg_port),
                    reg_port,
                )
        return set(found)
    finally:
        sock.close()


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


def docker_detected() -> bool:
    """running inside a container -> derived IPs are the NAT'd bridge address,
    useless for SIP: callers must ask the user for the host's real LAN IP."""
    if Path("/.dockerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text(errors="replace")
    except OSError:
        return False
    return any(marker in cgroup for marker in ("docker", "containerd", "kubepods", "podman"))


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
    candidates = [h for h in tcp_candidates(subnet) if h not in door_ips]
    answered = registrar_probe_many(candidates, timeout=min(timeout, 2.0), local_ip=local_ip)
    registrars = [h for h in candidates if h in answered]
    return {
        "doors": doors,
        "registrars": registrars,
        "candidates": [h for h in candidates if h not in answered],
    }


def _default_local_ip():
    for target in ("192.168.1.1", "10.0.0.1", "8.8.8.8"):
        try:
            return local_ip_for(target, 80)
        except OSError:
            continue
    return None


# --- config-flow status probes (vdm10-ha) ----------------------------------


def _reg_xml(serial: str, mac: str, name: str, floor: int = 1) -> str:
    # exact firmware field order (_sip_xml_get_reg_info_xml): serial, mac, name, floorNum
    return (
        f"<regXML>\r\n<regDevSerial>{serial}</regDevSerial>\r\n"
        f"<regDevMacAddr>{mac}</regDevMacAddr>\r\n"
        f"<regDevName>{name}</regDevName>\r\n"
        f"<floorNum>{floor}</floorNum>\r\n</regXML>"
    )


def _build_register(
    host: str, sipnum: str, local_ip: str, expires: int, serial: str, mac: str, port: int = REGISTRAR_PORT
) -> bytes:
    """Kind-1 indoor REGISTER per the VDM10 dialect (regXML body, replies are
    routed to the sender's port 5060 by the registrar — see module docstring).

    The body is sent even for ``Expires: 0``: the registrar's unregister
    delete is ``where serial_no='<s>' or dev_ip='<src ip>'`` and the serial
    is only scraped from regXML — an empty body matches nothing by serial.
    """
    body = _reg_xml(serial, mac, "vdm10-probe")
    return (
        f"REGISTER sip:{host}:{port} SIP/2.0\r\n"
        f"Via: SIP/2.0/UDP {local_ip}:{SIP_UA_PORT};rport;branch=z9hG4bK{random.randrange(1 << 31)}\r\n"
        f"Max-Forwards: 70\r\n"
        f"Contact: <sip:{sipnum}@{local_ip}:{SIP_UA_PORT}>\r\n"
        f"To: <sip:{sipnum}@{host}:{port}>\r\n"
        f'From: "vdm10-probe"<sip:{sipnum}@{local_ip}>;tag={random.randrange(1 << 31)}\r\n'
        f"Call-ID: {uuid.uuid4().hex}\r\n"
        f"CSeq: 1 REGISTER\r\n"
        f"User-Agent: eXosip/3.6.0\r\n"
        f"Allow: INVITE, ACK, CANCEL, BYE, REFER, INFO, OPTIONS, MESSAGE\r\n"
        f"Expires: {expires}\r\n"
        f"Content-Type: text/xml\r\n"
        f"Content-Length: {len(body.encode())}\r\n\r\n{body}"
    ).encode()


def _probe_datagram(sock: socket.socket, timeout: float, expect: str | None = None) -> bytes | None:
    """Wait for one SIP reply datagram; returns the raw packet or None.

    ``expect`` filters stray datagrams (e.g. a late unregister 200 for the
    previous slot) — the reply's To carries ``sip:<sipnum>@``, so only a
    datagram containing the probed number counts as its answer.
    """
    deadline = time.monotonic() + timeout
    while (remaining := deadline - time.monotonic()) > 0:
        ready, _, _ = select.select([sock], [], [], remaining)
        if not ready:
            return None
        try:
            data, _ = sock.recvfrom(65535)
        except OSError:
            continue
        if expect is not None and expect.encode() not in data:
            continue
        return data
    return None


def _probe_reply(sock: socket.socket, timeout: float, expect: str | None = None) -> str | None:
    """Like ``_probe_datagram`` but returns just the SIP status line."""
    data = _probe_datagram(sock, timeout, expect)
    if data is None:
        return None
    return data.split(b"\r\n", 1)[0].decode("utf-8", errors="replace")


def _auth_challenge(data: bytes) -> str | None:
    """The WWW-/Proxy-Authenticate value from a raw reply, for digest retry."""
    for raw in data.decode("utf-8", errors="replace").split("\r\n"):
        name, _, value = raw.partition(":")
        if name.lower() in ("www-authenticate", "proxy-authenticate"):
            return value.strip()
    return None


def _slot_status(
    sock: socket.socket,
    host: str,
    sipnum: str,
    local_ip: str,
    serial: str,
    mac: str,
    wait: float,
    port: int = REGISTRAR_PORT,
) -> int | None:
    """REGISTER ``sipnum`` at ``host:5065``; returns the reply status code.

    ``send_reply`` answers every outcome (200/410/400/500), so silence means
    the packet or its reply was lost — one retry before giving up. A 200
    inserts a dev_reg row; it is removed again with an ``Expires: 0``
    unregister carrying regXML (see ``_build_register``).
    """
    probe = _build_register(host, sipnum, local_ip, 600, serial, mac, port)
    line = None
    for attempt in range(2):
        sock.sendto(probe, (host, port))
        if line := _probe_reply(sock, wait if attempt == 0 else min(wait, 0.4), sipnum):
            break
    if not line or not line.startswith("SIP/2.0 "):
        return None
    code = int(line.split()[1])
    if code == 200:
        # every insert fires vns_registrarSyncHook — blocking :8102 connects
        # to each extension stall the registrar's SIP loop for seconds and
        # it drops datagrams meanwhile, so retry the unregister past that
        # window (bounded: an unanswered row still expires with its TTL and
        # the next same-IP REGISTER replaces it via the dev_ip dedup match).
        unregister = _build_register(host, sipnum, local_ip, 0, serial, mac, port)
        for _ in range(3):
            sock.sendto(unregister, (host, port))
            if _probe_reply(sock, 1.5, sipnum):
                break
    return code


def register_status(
    registrar: str,
    sipnum: str,
    serial: str,
    local_ip: str,
    port: int = REGISTRAR_PORT,
    wait: float = 0.8,
) -> int | None:
    """Probe-REGISTER ``sipnum`` on ``registrar`` and return the reply code.

    For the config flow's registration check: ``200`` = the extension can be
    claimed (the probe row is removed again via ``Expires: 0`` inside
    ``_slot_status``), ``410`` = held by a different device, ``401``/``403`` =
    the registrar challenged auth, ``None`` = unreachable or :5060 busy.
    ``serial`` should be the entry's own so the dedup verdict matches what
    the real registration will see.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", SIP_UA_PORT))
    except OSError:
        return None
    sock.setblocking(False)
    try:
        mac = f"bc:9b:5e:{random.randrange(1 << 24):06x}"
        return _slot_status(sock, registrar, sipnum, local_ip, serial, mac, wait, port)
    finally:
        sock.close()


def provider_status(
    host: str,
    port: int,
    user: str,
    password: str,
    domain: str,
    local_ip: str,
    timeout: float = 6.0,
) -> int | None:
    """REGISTER ``user`` at a standard SIP server; returns the reply code.

    For the config flow's provider/trunk check: REGISTER -> one digest retry
    on 401/407 -> status. A ``2xx`` is followed by ``Expires: 0`` cleanup.
    ``None`` = unreachable/timeout. Unlike the intercom registrar, providers
    answer on the Via's source port — an ephemeral bind suffices.
    """
    from metzler.sip.digest import digest_response  # lazy: module stays stdlib-only

    call_id, cseq = uuid.uuid4().hex, 1
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    try:
        sock.bind(("0.0.0.0", 0))
        bind_port = sock.getsockname()[1]

        def build(expires: int, auth: str | None = None) -> bytes:
            headers = (
                f"REGISTER sip:{domain} SIP/2.0\r\n"
                f"Via: SIP/2.0/UDP {local_ip}:{bind_port};rport;branch=z9hG4bK{random.randrange(1 << 31)}\r\n"
                f"Max-Forwards: 70\r\n"
                f"From: <sip:{user}@{domain}>;tag={random.randrange(1 << 31)}\r\n"
                f"To: <sip:{user}@{domain}>\r\n"
                f"Contact: <sip:{user}@{local_ip}:{bind_port}>\r\n"
                f"Call-ID: {call_id}\r\n"
                f"CSeq: {cseq} REGISTER\r\n"
                f"User-Agent: eXosip/3.6.0\r\n"
                f"Expires: {expires}\r\n"
                f"Content-Length: 0\r\n"
            )
            if auth:
                headers += f"Authorization: {auth}\r\n"
            return f"{headers}\r\n".encode()

        deadline = time.monotonic() + timeout
        sock.sendto(build(600), (host, port))
        code = None
        for attempt in range(3):
            data = _probe_datagram(sock, max(0.1, deadline - time.monotonic()), expect=call_id)
            if data is None:
                return None
            line = data.split(b"\r\n", 1)[0].decode("utf-8", errors="replace")
            if not line.startswith("SIP/2.0 "):
                return None
            code = int(line.split()[1])
            if code not in (401, 407) or attempt == 2:
                break
            hdr = _auth_challenge(data)
            if hdr is None:
                break
            cseq += 1
            auth = digest_response(hdr, "REGISTER", f"sip:{domain}", user, password)
            sock.sendto(build(600, auth), (host, port))
        if code is not None and 200 <= code < 300:
            cseq += 1
            sock.sendto(build(0), (host, port))
            _probe_datagram(sock, 1.0, expect=call_id)
        return code
    finally:
        sock.close()

"""tests for metzler.discovery - localhost fakes, nothing leaves 127.0.0.1."""

import re
import socket
import threading
import time

import pytest

from metzler import discovery
from metzler.discovery import (
    docker_detected,
    free_extension,
    is_vdm10_outdoor,
    local_ip_for,
    registrar_probe,
    sadp_probe,
    scan_extension_slots,
    subnet_of,
)

PROBE_MATCH = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<ProbeMatch><Uuid>x</Uuid><Types>inquiry</Types>"
    "<DeviceType>57619</DeviceType>"
    "<DeviceDescription>VDM10-VM-POE</DeviceDescription>"
    "<DeviceSN>VDM10-VM-POE0120220607RRK08753130</DeviceSN>"
    "<MAC>bc-9b-5e-01-a1-b3</MAC>"
    "<IPv4Address>10.0.0.48</IPv4Address>"
    "<SoftwareVersion>V2.2.56build 230608</SoftwareVersion>"
    "</ProbeMatch>"
)


def _bind():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.settimeout(5)
    return s


def fake_sadp(sock):
    """one-shot ProbeMatch responder on an already-bound UDP socket."""
    data, addr = sock.recvfrom(65535)
    assert b"<Types>inquiry</Types>" in data
    sock.sendto(PROBE_MATCH.encode(), addr)
    sock.close()


def fake_registrar(sock, statuses, got):
    """replies to REGISTERs per `statuses` dict {sipNum: code}; the firmware
    answers to source-ip:<via port>, which the test mirrors via the Via hf."""
    s = sock
    while True:
        try:
            data, addr = s.recvfrom(65535)
        except TimeoutError:
            break
        txt = data.decode(errors="replace")
        got.append(txt)
        if "REGISTER" not in txt.split("\r\n", 1)[0]:
            continue
        expires = "Expires: 0" in txt
        sip_num = txt.split("<sipNum>")[1].split("<")[0] if "<sipNum>" in txt else ""
        code = 200 if expires else statuses.get(sip_num, 410)
        via = txt.split("Via:")[1].split("\r\n")[0]
        reply = (
            f"SIP/2.0 {code} {'OK' if code == 200 else 'Sip Gone'}\r\n"
            f"Via:{via}\r\nCall-ID: {txt.split('Call-ID:')[1].split()[0]}\r\n"
            "CSeq: 1 REGISTER\r\nContent-Length: 0\r\n\r\n"
        )
        s.sendto(reply.encode(), addr)  # same socket: via port == source port


def test_sadp_probe_parses_probe_match():
    srv = _bind()
    port = srv.getsockname()[1]
    t = threading.Thread(target=fake_sadp, args=(srv,), daemon=True)
    t.start()
    out = sadp_probe(targets=[("127.0.0.1", port)], timeout=4, multicast=False)
    assert len(out) == 1
    m = out[0]
    assert m["DeviceType"] == "57619"
    assert m["DeviceDescription"] == "VDM10-VM-POE"
    assert m["MAC"] == "bc-9b-5e-01-a1-b3"
    assert is_vdm10_outdoor(m)
    t.join()


def test_is_vdm10_outdoor_by_mac():
    assert is_vdm10_outdoor({"MAC": "BC:9B:5E:AA:BB:CC"})
    assert not is_vdm10_outdoor({"MAC": "ec:71:db:aa:bb:cc"})
    assert not is_vdm10_outdoor({"DeviceType": "12345", "MAC": "aa:bb:cc:dd:ee:ff"})


def _registrar(statuses, got, port=0):
    srv = _bind()
    if port:
        srv.close()
        srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.settimeout(8)
    t = threading.Thread(target=fake_registrar, args=(srv, statuses, got), daemon=True)
    t.start()
    return srv.getsockname()[1], t


def test_registrar_probe_200_then_unregister():
    got = []
    reg_port, t = _registrar({"10000000000": 200}, got)
    st = registrar_probe("127.0.0.1", "10000000000", listen_port=0, reg_port=reg_port, local_ip="127.0.0.1", timeout=4)
    assert st == 200
    t.join(timeout=2)
    # the 200 inserts a row -> probe must follow with an Expires:0 unregister
    assert any("Expires: 0" in p or "expires: 0" in p.lower() for p in got)
    assert any("<sipNum>10000000000</sipNum>" in p for p in got)


def test_registrar_probe_410_means_taken():
    got = []
    reg_port, t = _registrar({"10000000001": 410}, got)
    st = registrar_probe("127.0.0.1", "10000000001", listen_port=0, reg_port=reg_port, local_ip="127.0.0.1", timeout=4)
    assert st == 410
    t.join(timeout=2)


def test_registrar_probe_silent_returns_none():
    free = _bind()
    dead_port = free.getsockname()[1]
    free.close()  # nothing listens there now
    st = registrar_probe("127.0.0.1", "10000000000", listen_port=0, reg_port=dead_port, local_ip="127.0.0.1", timeout=1)
    assert st is None


def test_scan_slots_finds_free_ext():
    got = []
    reg_port, t = _registrar(
        {"10000000001": 410, "10000000002": 200, "10000000003": 410, "10000000004": 410, "10000000005": 410}, got
    )
    res = scan_extension_slots(
        "127.0.0.1", slots=range(1, 6), listen_port=0, reg_port=reg_port, local_ip="127.0.0.1", timeout=3
    )
    assert res["10000000001"] == 410
    assert res["10000000002"] == 200
    t.join(timeout=2)


def test_free_extension_picks_lowest_free():
    got = []
    reg_port, t = _registrar(
        {
            "10000000000": 410,
            "10000000001": 410,
            "10000000002": 200,
            "10000000003": 200,
            "10000000004": 410,
            "10000000005": 410,
        },
        got,
    )
    ext = free_extension("127.0.0.1", listen_port=0, reg_port=reg_port, local_ip="127.0.0.1", timeout=3)
    assert ext == "10000000002"
    t.join(timeout=2)


def test_discover_localhost_registrar(_sip_ua_port_free):
    """end-to-end: fake :8000 TCP candidate + :5065 registrar -> found."""
    from metzler.discovery import discover

    got = []
    # fake :5065 registrar - discover() probes the real port on the subnet IPs
    _, reg_t = _registrar({"10000000000": 410}, got, port=5065)
    # fake TCP :8000 acceptor (the Hikvision SDK signature that gets a host
    # into the fingerprint stage)
    tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp.bind(("127.0.0.1", 8000))
    tcp.listen(4)
    tcp.settimeout(0.05)
    stop = threading.Event()

    def accepter():
        while not stop.is_set():
            try:
                c, _ = tcp.accept()
                c.close()
            except OSError:
                break

    threading.Thread(target=accepter, daemon=True).start()
    try:
        # registrar_probe inside discover binds :5060 - keep it free here
        res = discover(subnet=["127.0.0.1"], local_ip="127.0.0.1", timeout=3)
        assert res["registrars"] == ["127.0.0.1"]
        assert res["doors"] == []
        assert res["candidates"] == []
    finally:
        stop.set()
        tcp.close()
        reg_t.join(timeout=6)


def _registrar_on(ip, port, statuses, got):
    """fake_registrar bound to a specific loopback IP + port (multi-host)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind((ip, port))
    srv.settimeout(8)
    t = threading.Thread(target=fake_registrar, args=(srv, statuses, got), daemon=True)
    t.start()
    return t


def test_probe_many_finds_registrar():
    got = []
    reg_port, t = _registrar({"10000000000": 410}, got)
    found = discovery.registrar_probe_many(
        ["127.0.0.1"], listen_port=0, reg_port=reg_port, local_ip="127.0.0.1", timeout=2
    )
    assert found == {"127.0.0.1"}
    t.join(timeout=2)


def test_probe_many_two_hosts_share_one_socket():
    """two registrars at the same reg_port on different loopback IPs -> both
    answered; replies are matched by source addr on the shared socket."""
    got = []
    free = _bind()
    port = free.getsockname()[1]
    free.close()
    t1 = _registrar_on("127.0.0.1", port, {"10000000000": 410}, got)
    t2 = _registrar_on("127.0.0.2", port, {"10000000000": 410}, got)
    try:
        found = discovery.registrar_probe_many(
            ["127.0.0.1", "127.0.0.2"], listen_port=0, reg_port=port, local_ip="127.0.0.1", timeout=2
        )
        assert found == {"127.0.0.1", "127.0.0.2"}
    finally:
        t1.join(timeout=2)
        t2.join(timeout=2)


def test_probe_many_silent_host_bounds_the_sweep():
    """a dead host costs one timeout for the whole fingerprint, not per host."""
    free = _bind()
    dead = free.getsockname()[1]
    free.close()
    start = time.monotonic()
    found = discovery.registrar_probe_many(
        ["127.0.0.1", "127.0.0.2"], listen_port=0, reg_port=dead, local_ip="127.0.0.1", timeout=1.0
    )
    assert found == set()
    assert time.monotonic() - start < 2.5


def test_scan_slots_silent_host_bounds_the_sweep():
    """6 slots x per-slot timeout used to take ~36s on a dead host - the batch
    sweep must finish within its overall budget."""
    free = _bind()
    dead = free.getsockname()[1]
    free.close()
    start = time.monotonic()
    res = scan_extension_slots("127.0.0.1", listen_port=0, reg_port=dead, local_ip="127.0.0.1", timeout=1.0)
    assert set(res.values()) == {None}
    assert time.monotonic() - start < 2.5


def test_local_ip_and_subnet():
    assert local_ip_for("127.0.0.1", 5065) == "127.0.0.1"
    s = subnet_of("10.0.0.123")
    assert s[0] == "10.0.0.1" and s[-1] == "10.0.0.254" and len(s) == 254


def test_docker_detected(monkeypatch, tmp_path):
    from pathlib import Path
    from unittest.mock import patch

    real_exists, real_read = Path.exists, Path.read_text

    def fake_exists(self):
        return str(self) == "/.dockerenv" or real_exists(self)

    def fake_read(self, **kw):
        if str(self) == "/proc/1/cgroup":
            return "0::/"
        return real_read(self, **kw)

    with patch.object(Path, "exists", fake_exists):
        assert docker_detected() is True
    with patch.object(Path, "read_text", fake_read):
        assert docker_detected() is False
        with patch.object(Path, "read_text", lambda s, **k: "0::/docker/abc" if str(s) == "/proc/1/cgroup" else ""):
            assert docker_detected() is True


# --- config-flow status probes (register_status / provider_status) ---------


class _ToParsingRegistrar:
    """answers REGISTERs per `statuses` {call_num: code}; unlisted slots get
    200, Expires:0 always 200. Parses `To:` - the probe packet carries the
    number there (register_status/_slot_status dialect)."""

    def __init__(self, statuses: dict):
        self.statuses = statuses
        self.received: list[bytes] = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.settimeout(0.1)
        self.running = True
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(65535)
            except (TimeoutError, OSError):
                continue
            if not data.startswith(b"REGISTER"):
                continue
            self.received.append(data)
            head = data.decode("utf-8", "replace")
            m = re.search(r"To: <sip:(\d+)@", head)
            call_num = m.group(1) if m else ""
            code = 200 if "Expires: 0" in head else self.statuses.get(call_num, 200)
            reason = {200: "OK", 410: "Sip Gone", 400: "Bad Request"}[code]
            reply = f"SIP/2.0 {code} {reason}\r\nTo: <sip:{call_num}@127.0.0.1:{self.port}>;tag=x\r\n\r\n"
            self.sock.sendto(reply.encode(), addr)

    def close(self):
        self.running = False
        self.thread.join(timeout=1)
        self.sock.close()


@pytest.fixture
def _sip_ua_port_free():
    """register_status binds :5060 for the reply - skip when it's taken"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("127.0.0.1", discovery.SIP_UA_PORT))
    except OSError:
        pytest.skip("port 5060 busy")
    finally:
        s.close()


def test_probe_numbers_stay_11_chars():
    for slot in discovery.EXT_SLOTS:
        assert len(f"{discovery.EXT_BASE}{slot}") == 11


def test_build_register_keeps_regxml_on_unregister():
    pkt = discovery._build_register("127.0.0.1", "10000000001", "127.0.0.1", 0, "HAPROBE000001", "bc:9b:5e:00:00:01")
    assert b"Expires: 0" in pkt
    assert b"<regDevSerial>HAPROBE000001</regDevSerial>" in pkt


def test_probe_reply_expect_filters_stray_datagrams():
    """a late unregister-200 for a previous slot must not count as the answer
    to the next slot's probe - the reply To carries the probed number."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        addr = ("127.0.0.1", sock.getsockname()[1])
        sender.sendto(b"SIP/2.0 200 OK\r\nTo: <sip:10000000001@x>\r\n\r\n", addr)
        sender.sendto(b"SIP/2.0 410 Sip Gone\r\nTo: <sip:10000000002@x>\r\n\r\n", addr)
        line = discovery._probe_reply(sock, 1.0, expect="10000000002")
        assert line == "SIP/2.0 410 Sip Gone"
    finally:
        sock.close()
        sender.close()


def test_register_status_reports_held_slot(_sip_ua_port_free):
    reg = _ToParsingRegistrar({"10000000001": 410})
    try:
        assert discovery.register_status("127.0.0.1", "10000000001", "HAPROBE001", "127.0.0.1", port=reg.port) == 410
    finally:
        reg.close()


def test_register_status_200_cleans_the_probe_row(_sip_ua_port_free):
    reg = _ToParsingRegistrar({})
    try:
        assert discovery.register_status("127.0.0.1", "10000000002", "HAPROBE001", "127.0.0.1", port=reg.port) == 200
        assert any(b"Expires: 0" in p and b"10000000002" in p for p in reg.received)
    finally:
        reg.close()


def test_register_status_none_on_silence(_sip_ua_port_free):
    free = _bind()
    dead = free.getsockname()[1]
    free.close()
    assert discovery.register_status("127.0.0.1", "10000000001", "HAPROBE001", "127.0.0.1", port=dead, wait=0.2) is None


class _FakeProvider:
    """Standard-SIP registrar: challenges REGISTER with a digest 401, then
    accepts (or keeps rejecting when ``accept`` is False)."""

    def __init__(self, accept=True):
        self.accept = accept
        self.received: list[bytes] = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.settimeout(0.1)
        self.running = True
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(65535)
            except (TimeoutError, OSError):
                continue
            if not data.startswith(b"REGISTER"):
                continue
            self.received.append(data)
            head = data.decode("utf-8", "replace")
            m = re.search(r"Call-ID: (\S+)", head)
            cid = m.group(1) if m else ""
            if "Expires: 0" in head or (self.accept and "Authorization: Digest" in head):
                self.sock.sendto(f"SIP/2.0 200 OK\r\nCall-ID: {cid}\r\n\r\n".encode(), addr)
            else:
                self.sock.sendto(
                    (
                        f"SIP/2.0 401 Unauthorized\r\nCall-ID: {cid}\r\n"
                        f'WWW-Authenticate: Digest realm="test",nonce="abc123",qop=auth\r\n\r\n'
                    ).encode(),
                    addr,
                )

    def close(self):
        self.running = False
        self.thread.join(timeout=1)
        self.sock.close()


def test_provider_status_digest_then_unregister():
    srv = _FakeProvider()
    try:
        code = discovery.provider_status("127.0.0.1", srv.port, "u", "p", "test.local", "127.0.0.1", timeout=3.0)
        assert code == 200
        assert any(b"Authorization: Digest" in p for p in srv.received), "must answer the 401 challenge"
        assert any(b"Expires: 0" in p for p in srv.received), "200 must be cleaned up again"
    finally:
        srv.close()


def test_provider_status_auth_rejected():
    srv = _FakeProvider(accept=False)
    try:
        code = discovery.provider_status("127.0.0.1", srv.port, "u", "bad", "test.local", "127.0.0.1", timeout=3.0)
        assert code == 401
    finally:
        srv.close()


def test_provider_status_none_on_timeout():
    assert discovery.provider_status("127.0.0.1", 5998, "u", "p", "test.local", "127.0.0.1", timeout=0.5) is None

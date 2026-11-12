"""tests for metzler.discovery - localhost fakes, nothing leaves 127.0.0.1."""

import socket
import threading

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


def test_discover_localhost_registrar():
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

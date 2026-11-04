"""Runs the script itself (standalone, as a subprocess) between a fake
intercom caller and a fake Asterisk on localhost: no credentials -> no
REGISTER, SRTP offer refused with 488 -> plain retry, answer, BYE relay."""

import os
import socket
import subprocess
import sys
import time

import pytest

from conftest import free_udp_port
from metzler import __main__ as entry  # the standalone entry script
from metzler.media.srtp import srtp_available
from metzler.sip.message import Packet
from metzler.sip.sdp import sdp_answer_talkback

pytestmark = pytest.mark.integration


def recv(sock, start, timeout=5):
    sock.settimeout(timeout)
    while True:
        data, addr = sock.recvfrom(65535)
        p = Packet.parse(data.decode())
        if p.status_line.startswith(start):
            return p, addr


def reply(req, code, body=""):
    p = Packet()
    p.status_line = f"SIP/2.0 {code}"
    for k in ("Via", "From", "Call-ID", "CSeq"):
        p.set(k, req.get(k))
    p.set("To", req.get("To") + ";tag=pbx1")
    p.set("Contact", f"<sip:6000@{req.get('Via').split()[1].split(';')[0]}>")
    if body:
        p.set("Content-Type", "application/sdp")
    p.body = body
    p.set("Content-Length", str(len(body)))
    return str(p).encode()


@pytest.fixture
def sim(tmp_path):
    ports = {k: free_udp_port() for k in ("sim", "pbx", "door", "reg")}
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    rtsp_port = s.getsockname()[1]
    s.close()
    env_file = tmp_path / "trunk.env"
    env_file.write_text(
        f"INDOOR_SERVER=127.0.0.1\nINDOOR_SERVER_PORT={ports['reg']}\nINDOOR_USERNAME=10000000009\n"
        f"INDOOR_PASSWORD=x\nINDOOR_NAME=TrunkTest\nINDOOR_LOCAL_IP=127.0.0.1\n"
        f"INDOOR_LISTEN_PORT={ports['sim']}\nINDOOR_RTSP_PORT={rtsp_port}\n"
        f"INDOOR_SIP_HOST=127.0.0.1\nINDOOR_SIP_PORT={ports['pbx']}\nINDOOR_SIP_TARGET=6000\n"
        f"INDOOR_HEARTBEAT_INTERVAL=600\nINDOOR_DEBUG=1\nINDOOR_MJPEG=0\n"
    )
    pbx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    pbx.bind(("127.0.0.1", ports["pbx"]))
    door = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    door.bind(("127.0.0.1", ports["door"]))
    log = tmp_path / "sim.log"
    proc = subprocess.Popen(
        [sys.executable, entry.__file__],
        env={**os.environ, "INDOOR_ENV": str(env_file)},
        stdout=log.open("w"),
        stderr=subprocess.STDOUT,
    )
    time.sleep(1.5)
    yield ports, pbx, door, log
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()
    pbx.close()
    door.close()


def test_intercom_call_through_an_asterisk_style_trunk(sim):
    ports, pbx, door, log = sim
    sim_addr = ("127.0.0.1", ports["sim"])
    inv = Packet()
    inv.status_line = f"INVITE sip:10000000009@127.0.0.1:{ports['sim']} SIP/2.0"
    inv.set("Via", f"SIP/2.0/UDP 127.0.0.1:{ports['door']};branch=z9hG4bKdoor1")
    inv.set("From", "<sip:10000000000@127.0.0.1>;tag=door1")
    inv.set("To", f"<sip:10000000009@127.0.0.1:{ports['sim']}>")
    inv.set("Call-ID", "doorcall1")
    inv.set("CSeq", "20 INVITE")
    inv.set("Contact", f"<sip:10000000000@127.0.0.1:{ports['door']}>")
    inv.set("Subject", "This is a call to extension.")
    inv.set("Content-Type", "application/sdp")
    inv.body = sdp_answer_talkback("Q1", "127.0.0.1")
    inv.set("Content-Length", str(len(inv.body)))
    door.sendto(str(inv).encode(), sim_addr)

    i1, a = recv(pbx, "INVITE")
    assert i1.status_line == "INVITE sip:6000@127.0.0.1 SIP/2.0"
    assert "sip:10000000009@" in i1.get("From")
    srtp = srtp_available()
    if srtp:
        assert "RTP/SAVP" in i1.body and "a=crypto:1" in i1.body
        pbx.sendto(reply(i1, "488 Not Acceptable Here"), a)
        assert recv(pbx, "ACK")[0].get("CSeq") == "1 ACK"
        i2, a = recv(pbx, "INVITE")
        assert "RTP/AVP" in i2.body and "crypto" not in i2.body and i2.get("CSeq") == "2 INVITE"
    else:
        i2 = i1
    pbx.sendto(
        reply(
            i2,
            "200 OK",
            "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=-\r\nc=IN IP4 127.0.0.1\r\n"
            "t=0 0\r\nm=audio 47000 RTP/AVP 0\r\na=rtpmap:0 PCMU/8000\r\n",
        ),
        a,
    )
    recv(pbx, "ACK")
    ok, _ = recv(door, "SIP/2.0 200")
    assert "a=isSpecialType:2" in ok.body

    bye = Packet()
    bye.status_line = f"BYE sip:10000000009@127.0.0.1:{ports['sim']} SIP/2.0"
    bye.set("Via", f"SIP/2.0/UDP 127.0.0.1:{ports['door']};branch=z9hG4bKdoor2")
    bye.set("From", inv.get("From"))
    bye.set("To", ok.get("To"))
    bye.set("Call-ID", "doorcall1")
    bye.set("CSeq", "21 BYE")
    bye.set("Content-Length", "0")
    door.sendto(str(bye).encode(), sim_addr)
    assert recv(pbx, "BYE")[0].get("Call-ID") == i1.get("Call-ID")

    text = log.read_text(errors="replace")
    assert "trunk mode, provider REGISTER skipped" in text
    assert f"REGISTER sip:127.0.0.1:{ports['pbx']}" not in text

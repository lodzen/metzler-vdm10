"""Shared fixtures: real option parsing and a real IndoorStation bound to
localhost ephemeral ports. Nothing here talks to the intercom LAN or the
internet; outgoing SIP is captured instead of sent."""

import socket

import pytest

from metzler.cli import build_parser, finalize_options
from metzler.sip.message import Packet
from metzler.station import IndoorStation


def free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def make_opts(*argv, env=None):
    """parse argv through the script's own parser + finalize_options"""
    base = [
        "--server",
        "127.0.0.1",
        "--server-port",
        "5098",
        "--username",
        "10000000003",
        "--password",
        "pw",
        "--name",
        "Test-Room",
        "--serial",
        "Q12345678",
        "--local-ip",
        "127.0.0.1",
        "--listen-port",
        "0",
        "--rtsp-port",
        "0",
    ]
    ap = build_parser(env or {})
    o = ap.parse_args(base + list(argv))
    finalize_options(o, ap)
    return o


@pytest.fixture
def opts():
    return make_opts


class Sent(list):
    """records (packet, addr) for every station.send()"""

    def methods(self):
        return [
            p.status_line.split()[0] if not p.status_line.startswith("SIP/2.0") else p.status_line[8:11]
            for p, _ in self
        ]

    def first(self, start):
        return next(p for p, _ in self if p.status_line.startswith(start))


@pytest.fixture
def station_factory():
    made = []

    def build(*argv):
        # no provider given -> local headset mode with a test tone (no ALSA/ffmpeg
        # is touched until a call is answered)
        st = IndoorStation(make_opts(*(argv or ("--local-tone", "440"))))
        sent = st.sent = Sent()  # ty: ignore[unresolved-attribute]
        st.send = lambda pkt, addr: (pkt.set("Content-Length", str(len(pkt.body)), True), sent.append((pkt, addr)))  # ty: ignore[invalid-assignment]
        made.append(st)
        return st

    yield build
    for st in made:
        st.running = False
        for c in list(st.calls.values()):
            c.stop()
        st.sip.close()
        if st.rtsp:
            st.rtsp.running = False
            st.rtsp.ls.close()


@pytest.fixture
def trunk_station(station_factory):
    """provider mode toward a credential-less PBX on localhost (Asterisk style)"""
    return station_factory("--sip-host", "127.0.0.1", "--sip-port", "5099", "--sip-target", "6000")


@pytest.fixture
def linphone_station(station_factory):
    """provider mode with an account, like the sip.linphone.org setup"""
    return station_factory(
        "--sip-host",
        "127.0.0.1",
        "--sip-port",
        "5099",
        "--sip-domain",
        "sip.linphone.org",
        "--sip-user",
        "alice",
        "--sip-pass",
        "secret",
    )


def sip(text):
    """parse a SIP message written with \\n line ends"""
    return Packet.parse(text.replace("\n", "\r\n"))

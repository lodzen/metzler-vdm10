"""Packet codec, .env loading and option handling (Linphone, Asterisk trunk,
local headset mode and the stdlib-only standalone run)."""
import ast
import pathlib
import sys

import pytest
from conftest import make_opts, sip

from metzler import cli
from metzler.cli import build_parser
from metzler.config import env_bool, env_default, load_env
from metzler.media.srtp import srtp_available
from metzler.sip.message import sip_uri

PACKAGE = pathlib.Path(cli.__file__).parent


def test_packet_round_trip_keeps_header_order_and_repeats():
    p = sip("SIP/2.0 200 OK\nVia: SIP/2.0/UDP a;branch=1\nVia: SIP/2.0/UDP b;branch=2\n"
            "Call-ID: 42\nContent-Length: 4\n\nbody")
    assert p.get_all("via") == ["SIP/2.0/UDP a;branch=1", "SIP/2.0/UDP b;branch=2"]
    assert p.get("call-id") == "42" and p.body == "body"
    assert str(p).startswith("SIP/2.0 200 OK\r\nVia: SIP/2.0/UDP a;branch=1\r\nVia:")
    p.set("Content-Length", "9", replace=True)
    assert p.get_all("Content-Length") == ["9"]


@pytest.mark.parametrize("target, expected", [
    ("6000", "sip:6000@pbx.lan"),                       # Asterisk extension
    ("6001@other.host", "sip:6001@other.host"),
    ("sip:alice@sip.linphone.org", "sip:alice@sip.linphone.org"),
    ("sips:x@y", "sips:x@y"),
    ("", ""),
])
def test_sip_uri(target, expected):
    assert sip_uri(target, "pbx.lan") == expected


def test_env_file_parsing(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# comment\nINDOOR_SERVER=1.2.3.4\nINDOOR_NAME=\"Quoted Name\"\n"
                 "INDOOR_SIP_PASS=a=b#c\nINDOOR_DEBUG=on\nINDOOR_EMPTY=\n")
    env = load_env(str(f))
    assert env["INDOOR_NAME"] == "Quoted Name"
    assert env["INDOOR_SIP_PASS"] == "a=b#c"            # '=' and '#' inside a password survive
    assert env_default(env, "server") == "1.2.3.4"
    assert env_default(env, "empty", "fallback") == "fallback"
    assert env_default(env, "server_port", "5065", int) == 5065
    assert env_bool(env, "debug") and not env_bool(env, "nothing")
    assert load_env(str(tmp_path / "missing")) == {}


def test_env_supplies_parser_defaults():
    ap = build_parser({"INDOOR_SERVER": "10.0.0.38", "INDOOR_SIP_SRTP": "0",
                         "INDOOR_SPECIAL_TYPE": "0", "INDOOR_AUTO_ANSWER": "extension"})
    o = ap.parse_args([])
    assert (o.server, o.server_port, o.sip_srtp, o.special_type) == ("10.0.0.38", 5065, False, "0")
    assert o.auto_answer == "extension"


def test_linphone_target_defaults_to_the_account():
    o = make_opts("--sip-host", "sip.linphone.org", "--sip-user", "alice", "--sip-pass", "x")
    assert o.sip_domain == "sip.linphone.org"
    assert o.sip_target == "sip:alice@sip.linphone.org"


def test_asterisk_trunk_needs_no_account():
    o = make_opts("--sip-host", "192.168.0.20", "--sip-target", "6000")
    assert o.sip_target == "sip:6000@192.168.0.20"


def test_provider_mode_requires_a_target():
    with pytest.raises(SystemExit):
        make_opts("--sip-host", "192.168.0.20")


def test_local_headset_mode_needs_no_provider():
    o = make_opts("--local-tone", "440")
    assert o.sip_host is None


def test_srtp_degrades_to_plain_rtp_without_cryptography(monkeypatch):
    monkeypatch.setattr(cli, "srtp_available", lambda: False)
    o = make_opts("--sip-host", "h", "--sip-target", "6000")
    assert o.sip_srtp is False


def test_srtp_on_by_default_and_switchable():
    if not srtp_available():
        pytest.skip("cryptography not installed")
    assert make_opts("--sip-host", "h", "--sip-target", "6000").sip_srtp is True
    assert make_opts("--sip-host", "h", "--sip-target", "6000", "--no-sip-srtp").sip_srtp is False


def test_package_imports_only_the_stdlib_at_module_level():
    """standalone requirement: `python3 src/metzler/__main__.py` must work
    without any package; cryptography may only be imported lazily in functions"""
    allowed = set(sys.stdlib_module_names) | {"metzler"}
    for f in PACKAGE.rglob("*.py"):
        tree = ast.parse(f.read_text())
        top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        names = {a.name.split(".")[0] for n in top if isinstance(n, ast.Import) for a in n.names} | \
                {n.module.split(".")[0] for n in top if isinstance(n, ast.ImportFrom) and n.module}
        assert names <= allowed, (f.name, names - allowed)

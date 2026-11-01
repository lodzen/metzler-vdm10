"""Local headset mode, datagram routing, outbound answers and shutdown -
the parts that were split into small helpers during the restructure."""
import io
import struct

from conftest import sip
from test_station import CALLER, PROXY, intercom_invite, provider_invites, response

from metzler.media.intercom import IntercomMedia
from metzler.media.local import LocalMediaBridge
from metzler.sip.sdp import parse_sdp, sdp_answer_talkback
from metzler.station import Call


class FakeProc:
    """stands in for aplay/ffplay: collects what the bridge writes"""
    def __init__(self):
        self.stdin, self.stdout, self.stderr = io.BytesIO(), None, None

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0


def rtp(pt, payload):
    return struct.pack(">BBHII", 0x80, pt, 1, 160, 7) + payload


# ---------------------------------------------------------------- local media

def test_local_bridge_plays_door_audio_and_video(opts):
    bridge = LocalMediaBridge(opts("--local-tone", "440"), None, None)
    bridge.aplay, bridge.ffplay = FakeProc(), FakeProc()
    bridge.on_packet(bridge.audio_sock, rtp(0, b"\xff" * 160), ("10.0.0.9", 4000))
    bridge.on_packet(bridge.audio_sock, rtp(9, b"\x00" * 160), ("10.0.0.9", 4000))   # G.722: ignored
    bridge.on_packet(bridge.video_sock, rtp(96, b"NALU"), ("10.0.0.9", 4002))
    played, shown = bridge.aplay.stdin.getvalue(), bridge.ffplay.stdin.getvalue()
    bridge.stop()
    assert played == b"\x00\x00" * 160                     # 160 PCMU samples -> 320 B PCM (silence)
    assert shown == b"NALU"
    assert (bridge.door_a, bridge.door_v) == (("10.0.0.9", 4000), ("10.0.0.9", 4002))
    assert bridge.rx == {True: 2, False: 1}


def test_local_mode_rings_only_by_default(station_factory):
    st = station_factory("--local-tone", "440", "--answer-delay", "0")
    st.on_invite(intercom_invite(), CALLER)
    st.answer_locally(st.calls["extcall1"], None, None, parse_sdp(intercom_invite().body))
    assert [p.status_line for p, _ in st.sent] == ["SIP/2.0 100 Trying", "SIP/2.0 180 Ringing"]


def test_extension_policy_answers_extension_calls_not_doorbell_legs(station_factory):
    st = station_factory("--local-tone", "440", "--answer-delay", "0", "--auto-answer", "extension")
    door = intercom_invite("doorbell-leg")
    door.set("Subject", "This is a call to app.", replace=True)
    call = st.calls.setdefault("doorbell-leg", Call(st, door, CALLER))
    st.answer_locally(call, None, None, parse_sdp(door.body))
    assert call.state == "early"                           # doorbell leg keeps ringing for the real stations

    inv = intercom_invite()
    st.on_invite(inv, CALLER)
    call = st.calls["extcall1"]
    st.answer_locally(call, None, None, parse_sdp(inv.body))
    ok = st.sent.first("SIP/2.0 200")
    assert call.state == "established" and isinstance(call.local, IntercomMedia)
    assert ok.body == sdp_answer_talkback("Q12345678", "127.0.0.1")


# ---------------------------------------------------------------- routing

def test_datagram_routing(station_factory):
    st = station_factory()
    st.handle_datagram(b"\x00garbage", CALLER)             # unparseable: ignored
    st.handle_datagram(f"SIP/2.0 200 OK\r\nCall-ID: {st.reg_call_id}\r\nCSeq: 1 REGISTER\r\n\r\n".encode(),
                       ("127.0.0.1", 5098))
    assert st.reg_queue.get_nowait().status_line == "SIP/2.0 200 OK"
    st.handle_datagram(b"OPTIONS sip:x SIP/2.0\r\nVia: SIP/2.0/UDP 1.2.3.4;branch=o\r\n"
                       b"From: <sip:a@b>;tag=1\r\nTo: <sip:x@y>\r\nCall-ID: o1\r\nCSeq: 1 OPTIONS\r\n\r\n", CALLER)
    assert st.sent[-1][0].status_line == "SIP/2.0 200 OK"


def test_handler_errors_do_not_kill_the_receive_loop(station_factory):
    st = station_factory()
    st.on_info = lambda pkt, addr: 1 / 0
    st.handle_datagram(b"INFO sip:x SIP/2.0\r\nCall-ID: i\r\nCSeq: 1 INFO\r\n\r\n", CALLER)   # logged, not raised


# ---------------------------------------------------------------- outbound + shutdown

def test_outbound_intercom_answer_is_acked_at_the_contact(station_factory):
    st = station_factory()
    st.registered.set()
    st.call_out("10000000001@127.0.0.1")
    inv = st.sent[-1][0]
    answer = sip(f"SIP/2.0 200 OK\nVia: {inv.get('Via')}\nFrom: {inv.get('From')}\n"
                 f"To: {inv.get('To')};tag=47\nCall-ID: {inv.get('Call-ID')}\nCSeq: 1 INVITE\n"
                 f"Contact: <sip:10000000001@127.0.0.1:5061>\n\n")
    answer.body = sdp_answer_talkback("Q12345678", "127.0.0.1")
    st.on_response(answer, ("127.0.0.1", 5060))
    ack = st.sent.first("ACK")
    assert ack.status_line == "ACK sip:10000000001@127.0.0.1:5061 SIP/2.0"
    out = st.out_calls[inv.get("Call-ID")]
    assert out["established"] and isinstance(out["local"], IntercomMedia)


def test_shutdown_ends_both_legs_and_unregisters(linphone_station):
    st = linphone_station
    st.on_invite(intercom_invite(), CALLER)
    inv = provider_invites(st)[0]
    st.on_response(response(inv, "200 OK", "Content-Type: application/sdp",
                            "v=0\r\nc=IN IP4 10.9.9.9\r\nm=audio 41002 RTP/AVP 0\r\n"), PROXY)
    st.shutdown()
    caller_bye = next(p for p, a in st.sent if a == CALLER and p.status_line.startswith("BYE"))
    assert caller_bye.get("Call-ID") == "extcall1"
    assert any(p.status_line.startswith("BYE") and a == PROXY for p, a in st.sent)
    unreg = [p for p, a in st.sent if p.status_line.startswith("REGISTER")]
    assert {p.get("Expires") for p in unreg} == {"0"}      # intercom registrar and provider

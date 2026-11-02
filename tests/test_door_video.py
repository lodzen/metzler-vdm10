"""Door-call recognition + door video to the provider leg (early media).

A doorbell fan-out INVITE is recognized by the firmware's caller-class
(FUN_000f9ce0: devLongNum type 1), the 'call to app' Subject and a=doorType>0.
Only door calls get a video m= line; intercom calls stay audio-only. A 18x
response carrying SDP starts media streaming before the 200 OK."""

import socket
import struct

from conftest import sip
from metzler.media.intercom import IntercomVideoLeg
from metzler.media.srtp import srtp_available
from metzler.sip.sdp import parse_sdp

CALLER = ("127.0.0.1", 5097)
PROXY = ("127.0.0.1", 5099)


def door_invite(call_id="doorcall1"):
    """shape of the main indoor's door fan-out INVITE (.38 -> extension):
    From = door devLongNum, Subject 'call to app', doorType:1, no m= lines"""
    p = sip(
        "INVITE sip:10000000003@127.0.0.1:5060 SIP/2.0\n"
        "Via: SIP/2.0/UDP 127.0.0.1:5097;branch=z9hG4bKdoor\n"
        "From: <sip:10010100000@127.0.0.1>;tag=1\n"
        "To: <sip:10000000003@127.0.0.1:5060>\nCall-ID: doorcall1\nCSeq: 20 INVITE\n"
        "Contact: <sip:10010100000@127.0.0.1:5097>\n"
        "Subject: This is a call to app.\nContent-Type: application/sdp\n\n"
    )
    p.body = (
        "v=0\r\no=Q12345678 0 0 IN IP4 10.0.0.48\r\ns=Talk session\r\n"
        "c=IN IP4 10.0.0.48\r\nt=0 0\r\na=doorFloor:0\r\na=responseType:0\r\n"
        "a=doorType:1\r\na=isSpecialType:2\r\na=isConfirm:0\r\n"
    )
    return p


def provider_sdp(audio_port=47000, video_port=47002):
    body = (
        "v=0\r\no=- 1 1 IN IP4 10.9.9.9\r\ns=-\r\nc=IN IP4 10.9.9.9\r\nt=0 0\r\n"
        f"m=audio {audio_port} RTP/SAVP 0\r\na=rtpmap:0 PCMU/8000\r\n"
        f"m=video {video_port} RTP/SAVP 96\r\na=rtpmap:96 H264/90000\r\n"
    )
    if srtp_available():
        body += ""  # provider may answer without crypto -> bind_srtp drops to plain
    return body


def provider_response(req, code, body=""):
    p = sip(
        f"SIP/2.0 {code}\nVia: {req.get('Via')}\nFrom: {req.get('From')}\n"
        f"To: {req.get('To')};tag=prov1\nCall-ID: {req.get('Call-ID')}\n"
        f"CSeq: {req.get('CSeq')}\nContact: <sip:alice@10.9.9.9:41000>\n\n"
    )
    p.body = body
    return p


def prov_invite(st):
    return next(p for p, a in st.sent if p.status_line.startswith("INVITE") and a == PROXY)


# ------------------------------------------------------------ classification


def test_caller_class_matches_firmware_number_rules(trunk_station):
    cls = trunk_station._caller_class
    mk = lambda num: sip(f"INVITE sip:x@y SIP/2.0\nFrom: <sip:{num}@h>;tag=1\n\n")
    assert cls(mk("10000000000")) == 3  # main indoor
    assert cls(mk("10000000001")) == 3  # extension
    assert cls(mk("10010100000")) == 1  # door devLongNum (f3=01, f4=000)
    assert cls(mk("99999999999")) == 0  # not room, not door-type-1
    assert cls(mk("10010101000")) == 0  # f4!=0 -> another device class


def test_door_call_gets_a_video_leg_and_video_offer(trunk_station):
    st = trunk_station
    st.on_invite(door_invite(), CALLER)
    call = st.calls["doorcall1"]
    assert call.door and call.kind == "intercom"
    assert call.media.video is not None
    offer = parse_sdp(prov_invite(st).body)
    kinds = [m["type"] for m in offer["media"]]
    assert kinds == ["audio", "video"]
    proto = "RTP/SAVP" if srtp_available() else "RTP/AVP"
    assert f"m=video {call.media.video.rtp_port} {proto} 96" in prov_invite(st).body


def test_extension_call_stays_audio_only(linphone_station):
    from test_station import intercom_invite

    st = linphone_station
    st.on_invite(intercom_invite(), CALLER)
    call = st.calls["extcall1"]
    assert not call.door and call.media.video is None
    assert [m["type"] for m in parse_sdp(prov_invite(st).body)["media"]] == ["audio"]


def test_indoor_caller_with_doortype_stays_audio_only(trunk_station):
    """the main indoor stamps a=doorType:1 on extension calls too - a class-3
    caller is never a door call: no video m= line reaches the app"""
    st = trunk_station
    inv = door_invite()
    inv.set("From", "<sip:10000000000@127.0.0.1>;tag=2", replace=True)
    inv.set("Subject", "This is a call to extension.", replace=True)  # doorType:1 stays in body
    st.on_invite(inv, CALLER)
    call = st.calls["doorcall1"]
    assert not call.door and call.media.video is None
    assert [m["type"] for m in parse_sdp(prov_invite(st).body)["media"]] == ["audio"]


# ---------------------------------------------------------------- early media


def test_183_with_sdp_starts_media_before_the_200(trunk_station):
    st = trunk_station
    st.on_invite(door_invite(), CALLER)
    inv = prov_invite(st)
    call = st.calls["doorcall1"]
    assert not call.provider_media

    r183 = provider_response(inv, "183 Session Progress", provider_sdp())
    assert st.provider.handle_response(call, r183) == "early"
    st.early_media(call, r183)
    assert call.provider_media
    assert call.media.peer == ("10.9.9.9", 47000)
    assert call.media.video.peer == ("10.9.9.9", 47002)

    # the later 200 OK must not rebind/restart the streaming legs
    r200 = provider_response(inv, "200 OK", provider_sdp())
    assert st.provider.handle_response(call, r200) == "answered"
    st.provider_answered(call, r200)
    assert call.state == "established"


def test_180_without_sdp_stays_ringing(trunk_station):
    st = trunk_station
    st.on_invite(door_invite(), CALLER)
    call = st.calls["doorcall1"]
    inv = prov_invite(st)
    assert st.provider.handle_response(call, provider_response(inv, "180 Ringing")) == "ringing"
    assert not call.provider_media


# ------------------------------------------------------------- video repack


def test_video_leg_repacks_pulled_frames_as_pt96():
    leg = IntercomVideoLeg()
    sink = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sink.bind(("127.0.0.1", 0))
    sink.settimeout(2)
    leg.peer = sink.getsockname()
    frame = b"\x80\xe0" + struct.pack(">HII", 4004, 0xBEEF00, 0xD00D) + b"\x67H264PAY"
    leg.forward(frame)
    pkt, _ = sink.recvfrom(4096)
    assert pkt[0] == 0x80 and pkt[1] & 0x7F == 96 and pkt[1] & 0x80  # PT96, marker kept
    assert struct.unpack(">H", pkt[2:4])[0] == leg.seq - 1  # our seq, not 4004
    assert struct.unpack(">I", pkt[4:8])[0] == 0xBEEF00  # source ts kept
    assert struct.unpack(">I", pkt[8:12])[0] == leg.ssrc  # our ssrc
    assert pkt[12:] == b"\x67H264PAY"
    leg.close()
    sink.close()


def test_video_leg_drops_frames_until_a_peer_is_known():
    leg = IntercomVideoLeg()
    leg.forward(b"\x80\xe0" + b"\x00" * 20)  # no peer yet -> dropped silently
    assert leg.tx == 0
    leg.close()

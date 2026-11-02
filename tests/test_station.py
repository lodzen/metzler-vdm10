"""IndoorStation signalling against captured/derived firmware behaviour.
Outgoing packets are recorded (conftest.station_factory), never sent."""
import time

import pytest
from conftest import sip
from test_digest import LINPHONE_CHALLENGE

from metzler.media.srtp import SrtpSession, srtp_available
from metzler.sip.sdp import (
    media_addr,
    parse_sdp,
    sdp_answer_firmware,
    sdp_answer_talkback,
)

CALLER = ("127.0.0.1", 5097)
PROXY = ("127.0.0.1", 5099)


def intercom_invite(call_id="extcall1"):
    """the main indoor (10000000000) calling us 'to extension' - shape of the
    real INVITE .38 sent: header-only SDP, private attrs, Subject"""
    p = sip(f"INVITE sip:10000000003@127.0.0.1:5060 SIP/2.0\n"
            f"Via: SIP/2.0/UDP 127.0.0.1:5097;branch=z9hG4bKext\n"
            f"From: <sip:10000000000@127.0.0.1>;tag=864837168\n"
            f"To: <sip:10000000003@127.0.0.1:5060>\nCall-ID: {call_id}\nCSeq: 20 INVITE\n"
            f"Contact: <sip:10000000000@127.0.0.1:5097>\n"
            f"Subject: This is a call to extension.\nContent-Type: application/sdp\n\n")
    p.body = sdp_answer_talkback("Q12345678", "127.0.0.1")
    return p


def response(req, code, headers="", body=""):
    p = sip(f"SIP/2.0 {code}\nVia: {req.get('Via')}\nFrom: {req.get('From')}\n"
            f"To: {req.get('To')};tag=prov1\nCall-ID: {req.get('Call-ID')}\nCSeq: {req.get('CSeq')}\n"
            f"Contact: <sip:alice@10.9.9.9:41000>\n{headers}\n")
    p.body = body
    return p


def provider_invites(st):
    return [p for p, a in st.sent if p.status_line.startswith("INVITE") and a == PROXY]


# ---------------------------------------------------------------- registration

def test_reg_xml_matches_firmware_field_order(station_factory):
    st = station_factory()
    assert st.reg_xml() == ("<regXML>\r\n<regDevSerial>Q12345678</regDevSerial>\r\n"
                            "<regDevMacAddr>02:00:00:00:00:01</regDevMacAddr>\r\n"
                            "<regDevName>Test-Room</regDevName>\r\n<floorNum>0</floorNum>\r\n</regXML>")


def test_register_and_unregister(station_factory):
    st = station_factory()
    p = st.build_register()
    assert p.status_line == "REGISTER sip:127.0.0.1:5098 SIP/2.0"
    assert p.get("From").startswith('"Test-Room"<sip:10000000003@127.0.0.1>')
    assert p.get("To") == "<sip:10000000003@127.0.0.1:5098>"
    assert p.get("Content-Type") == "text/xml" and "<regXML>" in p.body
    assert p.get("User-Agent") == "eXosip/3.6.0"
    off = st.build_register(expires=0)
    assert off.get("Expires") == "0" and off.body == ""


def test_in_dialog_response_echoes_vias_and_adds_to_tag(station_factory):
    st = station_factory()
    inv = intercom_invite()
    inv.set("Via", "SIP/2.0/UDP 10.0.0.1;branch=2")
    r = st.in_dialog_response(inv, "180 Ringing", extra_to_tag="abc")
    assert r.get_all("Via") == inv.get_all("Via")
    assert r.get("To").endswith(";tag=abc") and r.get("CSeq") == "20 INVITE"


# ---------------------------------------------------------------- outbound calls

def test_call_out_direct_to_a_peer_bypasses_the_registrar(station_factory):
    """sip_callLongNum netAltRoute path: direct :5060 to the peer IP; .47
    only rang once the offer was header-only + Subject (registrar dropped m=)"""
    st = station_factory()
    st.registered.set()
    st.call_out("10000000001@10.0.0.47")
    p, addr = st.sent[-1]
    assert addr == ("10.0.0.47", 5060)
    assert p.status_line == "INVITE sip:10000000001@10.0.0.47:5060 SIP/2.0"
    assert p.get("Subject") == "This is a call to extension."
    assert parse_sdp(p.body)["media"] == [] and len(p.body) == len(sdp_answer_talkback(
        "Q12345678", "127.0.0.1"))


def test_call_out_via_registrar(station_factory):
    st = station_factory()
    st.registered.set()
    st.call_out("10000000001")
    assert st.sent[-1][1] == ("127.0.0.1", 5098)


def test_outbound_cancel_echoes_the_invites_branch_and_cseq(station_factory):
    """same RFC 3261 9.1 rule as the provider CANCEL: a fresh Via branch
    gets a 481 from the registrar and the intercom keeps ringing"""
    st = station_factory()
    st.registered.set()
    st.call_out("10000000001")
    inv = st.sent[-1][0]
    st.bye_outbound(inv.get("Call-ID"))
    can = st.sent[-1][0]
    assert can.status_line.startswith("CANCEL")
    assert can.get("Via") == inv.get("Via")
    assert can.get("CSeq") == "1 CANCEL"


# ---------------------------------------------------------------- incoming -> provider

def test_intercom_call_is_bridged_to_the_provider(linphone_station):
    st = linphone_station
    st.on_invite(intercom_invite(), CALLER)
    assert [p.status_line for p, a in st.sent if a == CALLER] == ["SIP/2.0 100 Trying",
                                                                 "SIP/2.0 180 Ringing"]
    inv = provider_invites(st)[0]
    assert inv.status_line == "INVITE sip:alice@sip.linphone.org SIP/2.0"
    assert inv.get("CSeq") == "1 INVITE"
    sdp = parse_sdp(inv.body)
    assert sdp["media"][0]["formats"][0] == "0"                    # PCMU
    if srtp_available():
        assert "RTP/SAVP" in inv.body and "a=crypto:1 AES_CM_128_HMAC_SHA1_80" in inv.body


def test_door_call_with_media_lines_uses_the_rtp_relay(trunk_station):
    """door stations offer real m= lines (firmware template) -> RtpRelay for
    audio and video, both re-offered on the provider leg"""
    st = trunk_station
    inv = intercom_invite("doorbell1")
    inv.body = sdp_answer_firmware("10000000000", "127.0.0.1", 30000, 30002)
    st.on_invite(inv, CALLER)
    call = st.calls["doorbell1"]
    assert call.kind == "door"
    assert (call.audio.peer_a, call.video.peer_a) == (("127.0.0.1", 30000), ("127.0.0.1", 30002))
    offer = parse_sdp(provider_invites(st)[0].body)
    assert media_addr(offer, "audio")[1] == call.audio.port_b
    assert media_addr(offer, "video")[1] == call.video.port_b


def test_trunk_mode_uses_station_number_and_extension_target(trunk_station):
    st = trunk_station
    assert not st.provider.needs_register()
    st.on_invite(intercom_invite(), CALLER)
    inv = provider_invites(st)[0]
    assert inv.status_line == "INVITE sip:6000@127.0.0.1 SIP/2.0"
    assert "<sip:10000000003@127.0.0.1>" in inv.get("From")


def test_provisional_responses_keep_the_call_alive(linphone_station):
    """regression: 100 Trying / 110 Push sent used to be treated as failure
    -> 486 to the main indoor -> 'call ended' + Hik-Connect 'declined' push"""
    st = linphone_station
    st.on_invite(intercom_invite(), CALLER)
    inv = provider_invites(st)[0]
    for code in ("100 Trying", "110 Push sent", "180 Ringing"):
        st.on_response(response(inv, code), PROXY)
    assert not any(p.status_line.startswith("SIP/2.0 486") for p, _ in st.sent)
    assert "extcall1" in st.calls


def test_407_is_acked_and_retried_with_proxy_authorization(linphone_station):
    st = linphone_station
    st.on_invite(intercom_invite(), CALLER)
    inv1 = provider_invites(st)[0]
    st.on_response(response(inv1, "407 Proxy Authentication Required",
                            f"Proxy-Authenticate: {LINPHONE_CHALLENGE}"), PROXY)
    ack = st.sent.first("ACK")
    assert ack.get("CSeq") == "1 ACK"
    assert ack.get("Via").split("branch=")[1] == inv1.get("Via").split("branch=")[1]
    inv2 = provider_invites(st)[1]
    assert inv2.get("CSeq") == "2 INVITE" and inv2.get("Call-ID") == inv1.get("Call-ID")
    assert inv2.get("Proxy-Authorization").startswith('Digest username="alice"')
    assert "algorithm=SHA-256" in inv2.get("Proxy-Authorization")
    assert inv2.get("Authorization") is None


def test_cancel_echoes_the_live_invites_branch_and_cseq(linphone_station):
    """a CANCEL only matches the INVITE transaction with the same Via branch
    and CSeq number (RFC 3261 9.1) - a fresh branch/CSeq makes the proxy
    answer 481 and the callee keeps ringing"""
    st = linphone_station
    st.on_invite(intercom_invite(), CALLER)
    st.on_response(response(provider_invites(st)[0], "407 Proxy Authentication Required",
                            f"Proxy-Authenticate: {LINPHONE_CHALLENGE}"), PROXY)
    inv = provider_invites(st)[-1]                      # the live (authed) INVITE
    st.provider.cancel(st.calls["extcall1"])
    can = st.sent.first("CANCEL")
    assert can.get("Via") == inv.get("Via")
    assert can.get("CSeq") == inv.get("CSeq").replace("INVITE", "CANCEL")
    assert can.status_line.split()[1] == inv.status_line.split()[1]   # same RURI


def test_401_uses_authorization_header(linphone_station):
    st = linphone_station
    st.on_invite(intercom_invite(), CALLER)
    st.on_response(response(provider_invites(st)[0], "401 Unauthorized",
                            f"WWW-Authenticate: {LINPHONE_CHALLENGE}"), PROXY)
    assert provider_invites(st)[1].get("Authorization")


def test_endless_challenges_give_up_with_busy(linphone_station):
    st = linphone_station
    st.on_invite(intercom_invite(), CALLER)
    for _ in range(3):
        st.on_response(response(provider_invites(st)[-1], "407 Proxy Authentication Required",
                                f"Proxy-Authenticate: {LINPHONE_CHALLENGE}"), PROXY)
    assert any(p.status_line == "SIP/2.0 486 Busy Here" and a == CALLER for p, a in st.sent)
    assert "extcall1" not in st.calls


@pytest.mark.skipif(not srtp_available(), reason="cryptography not installed")
def test_488_to_srtp_retries_once_with_plain_rtp_then_fails(trunk_station):
    st = trunk_station
    st.on_invite(intercom_invite(), CALLER)
    st.on_response(response(provider_invites(st)[0], "488 Not Acceptable Here"), PROXY)
    retry = provider_invites(st)[1]
    assert "RTP/AVP" in retry.body and "crypto" not in retry.body and retry.get("CSeq") == "2 INVITE"
    st.on_response(response(retry, "488 Not Acceptable Here"), PROXY)
    assert len(provider_invites(st)) == 2
    assert any(p.status_line == "SIP/2.0 486 Busy Here" and a == CALLER for p, a in st.sent)


def test_answer_completes_both_legs(linphone_station):
    st = linphone_station
    st.on_invite(intercom_invite(), CALLER)
    inv = provider_invites(st)[0]
    peer = SrtpSession() if srtp_available() else None
    body = ("v=0\r\no=- 1 1 IN IP4 10.9.9.9\r\ns=-\r\nc=IN IP4 10.9.9.9\r\nt=0 0\r\n"
            f"m=audio 41002 {'RTP/SAVP' if peer else 'RTP/AVP'} 0\r\n"
            + (peer.crypto_attr() if peer else "") + "a=rtpmap:0 PCMU/8000\r\n")
    st.on_response(response(inv, "200 OK", "Content-Type: application/sdp", body), PROXY)
    ok = next(p for p, a in st.sent if a == CALLER and p.status_line.startswith("SIP/2.0 200"))
    assert "a=isSpecialType:2" in ok.body and parse_sdp(ok.body)["media"] == []
    ack = st.sent.first("ACK")
    assert ack.status_line == "ACK sip:alice@10.9.9.9:41000 SIP/2.0"   # remote Contact
    call = st.calls["extcall1"]
    assert call.state == "established" and call.media.peer == ("10.9.9.9", 41002)
    if peer:
        assert call.media.srtp.rx is not None


def test_caller_hangup_cancels_or_byes_the_provider_leg(linphone_station):
    st = linphone_station
    inv = intercom_invite()
    st.on_invite(inv, CALLER)
    cancel = sip(f"CANCEL sip:10000000003@127.0.0.1 SIP/2.0\nVia: {inv.get('Via')}\n"
                 f"From: {inv.get('From')}\nTo: {inv.get('To')}\nCall-ID: extcall1\nCSeq: 20 CANCEL\n\n")
    st.on_cancel(cancel, CALLER)
    assert [p.status_line for p, a in st.sent if a == CALLER][-2:] == ["SIP/2.0 200 OK",
                                                                      "SIP/2.0 487 Request Terminated"]
    assert st.sent.first("CANCEL").get("Call-ID") == provider_invites(st)[0].get("Call-ID")
    assert st.calls == {} and st.provider.calls == {}


def test_provider_bye_ends_the_intercom_leg(linphone_station):
    st = linphone_station
    st.on_invite(intercom_invite(), CALLER)
    pcid = provider_invites(st)[0].get("Call-ID")
    st.on_bye(sip(f"BYE sip:alice@127.0.0.1 SIP/2.0\nVia: SIP/2.0/UDP 127.0.0.1:5099;branch=b\n"
                  f"From: <sip:alice@sip.linphone.org>;tag=prov1\nTo: <sip:x@y>;tag=t\n"
                  f"Call-ID: {pcid}\nCSeq: 3 BYE\n\n"), PROXY)
    bye = next(p for p, a in st.sent if a == CALLER and p.status_line.startswith("BYE"))
    assert bye.status_line == "BYE sip:10000000000@127.0.0.1:5097 SIP/2.0"
    assert bye.get("Call-ID") == "extcall1"
    assert st.calls == {}


# ---------------------------------------------------------------- loop guards

def test_own_fork_is_ignored_silently_even_after_the_call(linphone_station):
    """the proxy forks our INVITE back to our own contact: answering 486 ended
    the call before the pushed phone woke; late retransmits created a loop"""
    st = linphone_station
    st.on_invite(intercom_invite(), CALLER)
    pcid = provider_invites(st)[0].get("Call-ID")
    st.provider.calls.clear()                              # call already over
    n = len(st.sent)
    fork = intercom_invite(call_id=pcid)
    st.on_invite(fork, PROXY)
    assert len(st.sent) == n                               # no reply at all


def test_inbound_provider_call_is_declined_not_bridged_back(linphone_station):
    st = linphone_station
    st.on_invite(intercom_invite(call_id="from-linphone"), PROXY)
    assert [p.status_line for p, _ in st.sent] == ["SIP/2.0 486 Busy Here"]
    assert not provider_invites(st)


def test_provider_address_match_includes_the_port(trunk_station):
    """a PBX on the same host as an intercom station must not swallow its calls"""
    assert trunk_station.provider.is_provider_addr(PROXY)
    assert not trunk_station.provider.is_provider_addr(("127.0.0.1", 5060))


def test_nat_identity_is_learned_from_the_echoed_via(linphone_station):
    p = linphone_station.provider
    p._learn_public(sip("SIP/2.0 401 Unauthorized\nVia: SIP/2.0/UDP 10.0.0.96:5060;"
                        "rport=57474;branch=z9;received=198.51.100.7\n\n"))
    assert (p.sdp_ip(), p.sdp_port()) == ("198.51.100.7", 57474)


# ---------------------------------------------------------------- retransmits

def test_retransmitted_invite_resends_the_last_response(linphone_station):
    """a UDP retransmit of an INVITE in progress must not spawn a second Call:
    the last response sent for that transaction is resent instead"""
    st = linphone_station
    inv = intercom_invite()
    st.on_invite(inv, CALLER)
    n = len(st.sent)
    st.on_invite(inv, CALLER)
    assert len(st.calls) == 1 and len(provider_invites(st)) == 1
    assert [p.status_line for p, a in st.sent[n:] if a == CALLER] == ["SIP/2.0 180 Ringing"]


def test_retransmitted_invite_after_the_call_gets_the_final_response(linphone_station):
    st = linphone_station
    inv = intercom_invite()
    st.on_invite(inv, CALLER)
    cancel = sip(f"CANCEL sip:10000000003@127.0.0.1 SIP/2.0\nVia: {inv.get('Via')}\n"
                 f"From: {inv.get('From')}\nTo: {inv.get('To')}\nCall-ID: extcall1\n"
                 "CSeq: 20 CANCEL\n\n")
    st.on_cancel(cancel, CALLER)
    assert st.calls == {}
    n = len(st.sent)
    st.on_invite(inv, CALLER)
    assert [p.status_line for p, a in st.sent[n:] if a == CALLER] == \
        ["SIP/2.0 487 Request Terminated"]
    assert st.calls == {} and not provider_invites(st)[1:]


def test_own_ids_expire_so_the_loop_guard_cannot_grow_forever(linphone_station):
    p = linphone_station.provider
    p.own_ids["stale"] = time.time() - p.FORK_TTL - 1
    p.own_ids["fresh"] = time.time()
    assert not p.owns("stale") and "stale" not in p.own_ids
    assert p.owns("fresh")


def test_sip_from_sets_the_display_name_not_the_uri(station_factory):
    """providers (linphone.org) 403 a foreign From URI: --sip-from changes
    the display name only; a full 'u@h'/'Name <sip:u@h>' spec is verbatim"""
    st = station_factory("--sip-host", "127.0.0.1", "--sip-port", "5099",
                         "--sip-domain", "sip.linphone.org",
                         "--sip-user", "alice", "--sip-pass", "secret",
                         "--sip-from", "klingel")
    st.on_invite(intercom_invite(), CALLER)
    assert provider_invites(st)[0].get("From").startswith(
        '"klingel" <sip:alice@sip.linphone.org>;tag=')
    st = station_factory("--sip-host", "127.0.0.1", "--sip-port", "5099",
                         "--sip-domain", "sip.linphone.org",
                         "--sip-user", "alice", "--sip-pass", "secret",
                         "--sip-from", "Klingel <sip:klingel@sip.linphone.org>")
    st.on_invite(intercom_invite(), CALLER)
    assert provider_invites(st)[0].get("From").startswith(
        "Klingel <sip:klingel@sip.linphone.org>;tag=")


def test_sip_from_num_gives_each_station_its_own_caller_id(station_factory):
    """--sip-from {num}: the display name carries the calling station's
    intercom number, so the app can tell stations apart"""
    st = station_factory("--sip-host", "127.0.0.1", "--sip-port", "5099",
                         "--sip-domain", "sip.linphone.org",
                         "--sip-user", "alice", "--sip-pass", "secret",
                         "--sip-from", "{num}")
    st.on_invite(intercom_invite(), CALLER)      # From: sip:10000000000@127.0.0.1
    inv = provider_invites(st)[0]
    assert inv.get("From").startswith('"10000000000" <sip:alice@sip.linphone.org>;tag=')
    assert inv.get("Subject") == "10000000000"


def test_sip_names_maps_station_numbers_to_display_names(station_factory):
    """--sip-from {name}: --sip-names gives each station its contact name"""
    st = station_factory("--sip-host", "127.0.0.1", "--sip-port", "5099",
                         "--sip-domain", "sip.linphone.org",
                         "--sip-user", "alice", "--sip-pass", "secret",
                         "--sip-from", "{name}",
                         "--sip-names", "10000000000=Oben")
    st.on_invite(intercom_invite(), CALLER)
    inv = provider_invites(st)[0]
    assert inv.get("From").startswith('"Oben" <sip:alice@sip.linphone.org>;tag=')
    assert inv.get("Subject") == "Oben"


# ---------------------------------------------------------------- inbound calls

def inbound_invite(call_id="appcall1"):
    """the Linphone app dialing our registered account: real SDP offer"""
    return sip(f"INVITE sip:alice@sip.linphone.org SIP/2.0\n"
               f"Via: SIP/2.0/UDP 127.0.0.1:5099;branch=z9app\n"
               f"From: <sip:alice@sip.linphone.org>;tag=app1\n"
               f"To: <sip:alice@sip.linphone.org>\nCall-ID: {call_id}\nCSeq: 1 INVITE\n"
               f"Contact: <sip:alice@10.9.9.9:41000>\nContent-Type: application/sdp\n\n"
               "v=0\r\no=- 1 1 IN IP4 10.9.9.9\r\ns=-\r\nc=IN IP4 10.9.9.9\r\nt=0 0\r\n"
               "m=audio 41002 RTP/AVP 0\r\na=rtpmap:0 PCMU/8000\r\n"
               "m=video 41004 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n")


def inbound_station(station_factory):
    return station_factory("--sip-host", "127.0.0.1", "--sip-port", "5099",
                           "--sip-domain", "sip.linphone.org",
                           "--sip-user", "alice", "--sip-pass", "secret",
                           "--inbound-target", "10000000001")


def test_inbound_provider_call_rings_the_intercom_target(station_factory):
    st = inbound_station(station_factory)
    st.registered.set()
    st.on_invite(inbound_invite(), PROXY)
    assert [p.status_line for p, a in st.sent if a == PROXY] == \
        ["SIP/2.0 100 Trying", "SIP/2.0 180 Ringing"]
    out_inv = st.sent.first("INVITE sip:10000000001")
    assert out_inv and st.calls["appcall1"].out_cid == out_inv.get("Call-ID")
    # intercom 18x -> the app hears ringing
    st.on_response(response(out_inv, "180 Ringing"), ("127.0.0.1", 5098))
    assert [p.status_line for p, a in st.sent if a == PROXY][-1] == "SIP/2.0 180 Ringing"


def test_inbound_call_answered_bridges_both_legs(station_factory):
    st = inbound_station(station_factory)
    st.registered.set()
    st.on_invite(inbound_invite(), PROXY)
    out_inv = st.sent.first("INVITE sip:10000000001")
    st.on_response(response(out_inv, "200 OK"), ("127.0.0.1", 5098))
    ok = next(p for p, a in st.sent if a == PROXY and p.status_line == "SIP/2.0 200 OK")
    sdp = parse_sdp(ok.body)
    assert sdp["media"][0]["port"] > 0            # audio accepted
    assert sdp["media"][1]["port"] == 0           # video rejected
    assert st.calls["appcall1"].state == "established"


def test_app_bye_ends_the_intercom_leg(station_factory):
    st = inbound_station(station_factory)
    st.registered.set()
    st.on_invite(inbound_invite(), PROXY)
    out_inv = st.sent.first("INVITE sip:10000000001")
    st.on_response(response(out_inv, "200 OK"), ("127.0.0.1", 5098))
    st.on_bye(sip("BYE sip:alice@127.0.0.1 SIP/2.0\nVia: SIP/2.0/UDP 127.0.0.1:5099;branch=b\n"
                  "From: <sip:alice@sip.linphone.org>;tag=app1\nTo: <sip:x@y>;tag=t\n"
                  "Call-ID: appcall1\nCSeq: 2 BYE\n\n"), PROXY)
    bye = next(p for p, a in st.sent if a == ("127.0.0.1", 5098)
               and p.status_line.startswith("BYE"))
    assert bye.get("Call-ID") == out_inv.get("Call-ID")
    assert "appcall1" not in st.calls and st.out_calls == {}


def test_intercom_bye_ends_the_app_leg(station_factory):
    st = inbound_station(station_factory)
    st.registered.set()
    st.on_invite(inbound_invite(), PROXY)
    out_inv = st.sent.first("INVITE sip:10000000001")
    st.on_response(response(out_inv, "200 OK"), ("127.0.0.1", 5098))
    st.on_bye(sip(f"BYE sip:10000000003@127.0.0.1 SIP/2.0\nVia: SIP/2.0/UDP 127.0.0.1:5098;branch=b\n"
                  f"From: <sip:10000000001@127.0.0.1>;tag=in1\nTo: <sip:x@y>;tag=t\n"
                  f"Call-ID: {out_inv.get('Call-ID')}\nCSeq: 2 BYE\n\n"), ("127.0.0.1", 5098))
    bye = next(p for p, a in st.sent if a == PROXY and p.status_line.startswith("BYE"))
    assert bye.get("Call-ID") == "appcall1"
    assert "appcall1" not in st.calls


def test_busy_intercom_486es_the_app(station_factory):
    st = inbound_station(station_factory)
    st.registered.set()
    st.on_invite(inbound_invite(), PROXY)
    st.on_response(response(st.sent.first("INVITE sip:10000000001"),
                            "486 Busy Here"), ("127.0.0.1", 5098))
    assert [p.status_line for p, a in st.sent if a == PROXY][-1] == "SIP/2.0 486 Busy Here"
    assert "appcall1" not in st.calls


# ---------------------------------------------------------------- fritz trunk

FRITZ = ("127.0.0.1", 5077)


def fritz_station(station_factory):
    """provider mode + a Fritz!Box as second (inbound-only) trunk"""
    return station_factory("--sip-host", "127.0.0.1", "--sip-port", "5099",
                           "--sip-domain", "sip.linphone.org",
                           "--sip-user", "alice", "--sip-pass", "secret",
                           "--inbound-target", "10000000001",
                           "--fritz", "--fritz-host", "127.0.0.1",
                           "--fritz-port", "5077",
                           "--fritz-user", "620", "--fritz-pass", "pw")


def test_fritz_registers_as_its_own_account(station_factory):
    st = fritz_station(station_factory)
    p = st.fritz._register_pkt()
    assert p.status_line == "REGISTER sip:fritz.box SIP/2.0"
    assert p.get("From").startswith("<sip:620@fritz.box>")
    assert p.get("Contact").startswith("<sip:620@127.0.0.1:")
    assert st.fritz.label == "fritz" and st.provider.label == "provider"


def test_fritz_reg_response_routes_to_its_own_queue(station_factory):
    """two REGISTER loops share the socket; replies go to their own Call-ID"""
    st = fritz_station(station_factory)
    st.handle_datagram(f"SIP/2.0 200 OK\nCall-ID: {st.fritz.reg_call_id}\n\n".encode(), FRITZ)
    assert st.fritz.reg_queue.get_nowait().status_line.startswith("SIP/2.0 200")
    assert st.provider.reg_queue.empty()


def test_fritz_call_bridges_to_the_intercom_target(station_factory):
    """an INVITE from the fritz addr is an inbound SIP call: 100/180 + the
    intercom target rings; the call remembers its trunk"""
    st = fritz_station(station_factory)
    st.registered.set()
    st.on_invite(inbound_invite(), FRITZ)
    assert [p.status_line for p, a in st.sent if a == FRITZ] == \
        ["SIP/2.0 100 Trying", "SIP/2.0 180 Ringing"]
    assert st.sent.first("INVITE sip:10000000001")
    assert st.calls["appcall1"].trunk is st.fritz
    # ... while a call from the provider addr still uses the provider trunk
    st.on_invite(inbound_invite("appcall2"), PROXY)
    assert st.calls["appcall2"].trunk is st.provider


def test_fritz_leg_answers_with_the_fritz_identity(station_factory):
    """the 200 OK SDP must carry this trunk's address - not the provider's
    learned NAT identity"""
    st = fritz_station(station_factory)
    st.registered.set()
    st.provider.public_ip, st.provider.public_port = "198.51.100.7", 57474
    st.on_invite(inbound_invite(), FRITZ)
    out_inv = st.sent.first("INVITE sip:10000000001")
    st.on_response(response(out_inv, "200 OK"), ("127.0.0.1", 5098))
    ok = next(p for p, a in st.sent if a == FRITZ and p.status_line == "SIP/2.0 200 OK")
    assert "c=IN IP4 127.0.0.1" in ok.body and "198.51.100.7" not in ok.body


# ----------------------------------------------------- door race to the fritzfon

FRITZ_SDP = ("v=0\r\no=- 1 1 IN IP4 10.0.0.30\r\ns=-\r\nc=IN IP4 10.0.0.30\r\n"
             "t=0 0\r\nm=audio 40000 RTP/AVP 0\r\na=rtpmap:0 PCMU/8000\r\n")


def fritz_door_station(station_factory):
    """provider + fritz trunk: the fritzfon races every bridged call"""
    return station_factory("--sip-host", "127.0.0.1", "--sip-port", "5099",
                           "--sip-domain", "sip.linphone.org",
                           "--sip-user", "alice", "--sip-pass", "secret",
                           "--fritz", "--fritz-host", "127.0.0.1", "--fritz-port", "5077",
                           "--fritz-user", "12345678", "--fritz-pass", "pw",
                           "--fritz-call-button", "1")


def fritz_invites(st):
    return [p for p, a in st.sent if a == FRITZ and p.status_line.startswith("INVITE")]


def test_door_call_rings_the_fritzfon_in_parallel(station_factory):
    from test_door_video import door_invite
    st = fritz_door_station(station_factory)
    st.on_invite(door_invite(), CALLER)
    call = st.calls["doorcall1"]
    inv = fritz_invites(st)[0]
    assert inv.status_line == "INVITE sip:1@fritz.box SIP/2.0"   # the Klingeltaste
    assert "m=video" not in inv.body and "m=audio" in inv.body
    assert "RTP/AVP" in inv.body                            # no SRTP on the fritz leg
    assert call.fritz_leg["cid"] == inv.get("Call-ID") in st.fritz.own_ids
    assert call.trunk is st.provider                        # prov_* still = provider leg


def test_fritz_own_door_fork_is_ignored(station_factory):
    """we registered as the fritzfon's account - the box forks our own
    INVITE right back at us; that copy must not reach the inbound bridge"""
    from test_door_video import door_invite
    st = fritz_door_station(station_factory)
    st.on_invite(door_invite(), CALLER)
    cid = st.calls["doorcall1"].fritz_leg["cid"]
    n = len(st.sent)
    fork = sip(f"INVITE sip:12345678@127.0.0.1 SIP/2.0\n"
               f"Via: SIP/2.0/UDP 127.0.0.1:5077;branch=fork\nFrom: <sip:a@b>;tag=1\n"
               f"To: <sip:x@y>\nCall-ID: {cid}\nCSeq: 1 INVITE\n\n")
    st.on_invite(fork, FRITZ)
    assert len(st.sent) == n and "doorcall1" in st.calls


def test_fritz_wins_the_door_race(station_factory):
    """fritzfon answers first: provider leg is CANCELLed, the fritz leg is
    promoted into prov_* (ACK/teardown via call.trunk=fritz)"""
    from test_door_video import door_invite
    st = fritz_door_station(station_factory)
    st.on_invite(door_invite(), CALLER)
    call = st.calls["doorcall1"]
    prov_via = provider_invites(st)[-1].get("Via")
    finv = fritz_invites(st)[0]
    st.on_response(response(finv, "200 OK", body=FRITZ_SDP), FRITZ)
    can = next(p for p, a in st.sent if a == PROXY and p.status_line.startswith("CANCEL"))
    assert can.get("Via") == prov_via                        # killed the racing leg
    assert next(p for p, a in st.sent if a == FRITZ and p.status_line.startswith("ACK"))
    assert call.trunk is st.fritz and call.state == "established"
    assert call.fritz_leg is None and call.prov_call_id == finv.get("Call-ID")
    assert any(p.status_line == "SIP/2.0 200 OK" and a == CALLER for p, a in st.sent)


def test_provider_wins_the_door_race(station_factory):
    """app answers first: the fritz leg is CANCELLed with its own branch/CSeq"""
    from test_door_video import door_invite, provider_sdp
    st = fritz_door_station(station_factory)
    st.on_invite(door_invite(), CALLER)
    call = st.calls["doorcall1"]
    finv = fritz_invites(st)[0]
    st.on_response(response(provider_invites(st)[0], "200 OK",
                            body=provider_sdp()), PROXY)
    can = next(p for p, a in st.sent if a == FRITZ and p.status_line.startswith("CANCEL"))
    assert can.get("Via") == finv.get("Via")
    assert can.get("CSeq") == finv.get("CSeq").replace("INVITE", "CANCEL")
    assert call.fritz_leg is None and call.trunk is st.provider


def test_fritz_win_after_provider_early_media_rebinds_media_once(station_factory):
    """provider 183 already started media (RTSP puller running); when the
    fritz leg then wins, peers rebind to the fritz box - but start() must
    not spawn a second puller (the old one used to keep pulling the camera
    forever) and the stale provider video peer must die"""
    from test_door_video import door_invite, provider_sdp
    st = fritz_door_station(station_factory)
    st.on_invite(door_invite(), CALLER)
    call = st.calls["doorcall1"]
    st.on_response(response(provider_invites(st)[0], "183 Session Progress",
                            body=provider_sdp()), PROXY)
    assert call.provider_media and call.media.puller
    puller = call.media.puller
    st.on_response(response(fritz_invites(st)[0], "200 OK", body=FRITZ_SDP), FRITZ)
    assert call.trunk is st.fritz and call.state == "established"
    assert call.media.puller is puller                    # not replaced/leaked
    assert call.media.peer == ("10.0.0.30", 40000)   # audio rebound to fritz
    assert call.media.video.peer is None                  # dead leg's video peer cleared


def test_fritz_leg_bye_ends_the_intercom_call(station_factory):
    """fritzfon won the race and hangs up: its BYE arrives on the fritz
    Call-ID (which lives in fritz.calls, not provider.calls) and must
    tear down the intercom leg too"""
    st = fritz_door_station(station_factory)
    st.on_invite(intercom_invite(), CALLER)
    call = st.calls["extcall1"]
    finv = fritz_invites(st)[0]
    st.on_response(response(finv, "200 OK", body=FRITZ_SDP), FRITZ)
    assert call.trunk is st.fritz
    st.on_bye(sip(f"BYE sip:x@127.0.0.1 SIP/2.0\nVia: SIP/2.0/UDP 127.0.0.1:5077;branch=b\n"
                  f"From: <sip:a@b>;tag=1\nTo: <sip:x@y>;tag=t\n"
                  f"Call-ID: {finv.get('Call-ID')}\nCSeq: 2 BYE\n\n"), FRITZ)
    bye = next(p for p, a in st.sent if a == CALLER and p.status_line.startswith("BYE"))
    assert bye.get("Call-ID") == "extcall1"
    assert "extcall1" not in st.calls


def test_provider_bye_while_the_fritz_leg_races_cancels_it(station_factory):
    """app-side BYE on a still-racing call must CANCEL the fritz leg too -
    otherwise the fritzfon keeps ringing an orphaned INVITE"""
    st = fritz_door_station(station_factory)
    st.on_invite(intercom_invite(), CALLER)
    pcid = provider_invites(st)[0].get("Call-ID")
    st.on_bye(sip(f"BYE sip:x@127.0.0.1 SIP/2.0\nVia: SIP/2.0/UDP 127.0.0.1:5099;branch=b\n"
                  f"From: <sip:a@b>;tag=1\nTo: <sip:x@y>;tag=t\n"
                  f"Call-ID: {pcid}\nCSeq: 3 BYE\n\n"), PROXY)
    assert any(p.status_line.startswith("CANCEL") and a == FRITZ for p, a in st.sent)
    assert "extcall1" not in st.calls


def test_fritz_leg_loss_is_not_fatal_while_the_app_rings(station_factory):
    """a transport-ish leg failure (timeout, error) drops only that leg -
    the remaining racing leg keeps ringing"""
    from test_door_video import door_invite, provider_sdp
    st = fritz_door_station(station_factory)
    st.on_invite(door_invite(), CALLER)
    call = st.calls["doorcall1"]
    st.on_response(response(fritz_invites(st)[0], "408 Request Timeout"), FRITZ)
    assert call.fritz_leg is None and "doorcall1" in st.calls   # app still rings
    assert not any(p.status_line.startswith("SIP/2.0 486") for p, _ in st.sent)
    st.on_response(response(provider_invites(st)[0], "200 OK",
                            body=provider_sdp()), PROXY)
    assert call.state == "established"


def test_linphone_decline_ends_the_whole_race(station_factory):
    """an explicit decline on the app is a user rejection, not a device
    failure: the fritz leg is CANCELLed and the indoor sees the decline"""
    st = fritz_door_station(station_factory)
    st.on_invite(intercom_invite(), CALLER)
    st.on_response(response(provider_invites(st)[0], "603 Decline"), PROXY)
    assert any(p.status_line.startswith("CANCEL") and a == FRITZ for p, a in st.sent)
    assert any(p.status_line == "SIP/2.0 603 Decline" and a == CALLER for p, a in st.sent)
    assert "extcall1" not in st.calls


def test_fritz_decline_ends_the_whole_race(station_factory):
    """same on the fritz leg: an explicit decline there cancels the still
    ringing provider leg instead of leaving it orphaned"""
    st = fritz_door_station(station_factory)
    st.on_invite(intercom_invite(), CALLER)
    st.on_response(response(fritz_invites(st)[0], "603 Decline"), FRITZ)
    assert any(p.status_line.startswith("CANCEL") and a == PROXY for p, a in st.sent)
    assert any(p.status_line == "SIP/2.0 603 Decline" and a == CALLER for p, a in st.sent)
    assert "extcall1" not in st.calls


def test_dead_leg_final_response_still_gets_acked(station_factory):
    """after a leg is torn down its peer keeps retransmitting the final
    response until it sees the transaction ACK - which we must still send
    (the branch sits in the response's bottom Via)"""
    st = fritz_door_station(station_factory)
    st.on_invite(intercom_invite(), CALLER)
    finv = fritz_invites(st)[0]
    st.on_response(response(fritz_invites(st)[0], "603 Decline"), FRITZ)
    st.sent.clear()
    st.on_response(response(finv, "487 Request Terminated"), FRITZ)   # retransmit
    ack = next(p for p, a in st.sent if a == FRITZ and p.status_line.startswith("ACK"))
    assert ack.get("Call-ID") == finv.get("Call-ID")
    assert ack.get("CSeq") == finv.get("CSeq").replace("INVITE", "ACK")
    assert finv.get("Via").split("branch=")[1] in ack.get("Via")


def test_dead_provider_leg_is_not_cancelled_again(station_factory):
    """indoor hangs up after the provider leg already failed: CANCEL goes to
    the still-live fritz leg but NOT to the dead provider transaction (481)"""
    st = fritz_door_station(station_factory)
    st.on_invite(intercom_invite(), CALLER)
    st.on_response(response(provider_invites(st)[0], "408 Request Timeout"), PROXY)
    st.sent.clear()
    st.on_bye(sip("BYE sip:x@127.0.0.1 SIP/2.0\nVia: SIP/2.0/UDP 127.0.0.1:5097;branch=b\n"
                  "From: <sip:a@b>;tag=1\nTo: <sip:x@y>;tag=t\n"
                  "Call-ID: extcall1\nCSeq: 21 BYE\n\n"), CALLER)
    assert any(p.status_line.startswith("CANCEL") and a == FRITZ for p, a in st.sent)
    assert not any(p.status_line.startswith("CANCEL") and a == PROXY for p, a in st.sent)


def test_outbound_leg_failure_is_acked(station_factory):
    """the registrar retransmits a non-2xx final on our outbound INVITE until
    it sees the transaction ACK - ~30s of 603/487 noise otherwise"""
    st = station_factory("--sip-host", "127.0.0.1", "--sip-port", "5099",
                         "--sip-domain", "sip.linphone.org",
                         "--sip-user", "alice", "--sip-pass", "secret")
    st.registered.set()
    st.call_out("10000000000")
    inv = next(p for p, a in st.sent if a == st.server and p.status_line.startswith("INVITE"))
    st.on_response(response(inv, "603 Decline"), st.server)
    ack = next(p for p, a in st.sent if a == st.server and p.status_line.startswith("ACK"))
    assert ack.get("Call-ID") == inv.get("Call-ID") and ack.get("CSeq") == "1 ACK"


def test_stale_call_is_reaped_with_a_408(station_factory):
    """caller crashed/vanished mid-ring (no CANCEL, no final response on any
    leg): the janitor ends the call - 408 clears the registrar's busy flag,
    live legs get CANCELLed, media stops"""
    st = fritz_door_station(station_factory)
    st.on_invite(intercom_invite(), CALLER)
    call = st.calls["extcall1"]
    call.created = time.time() - st.RING_TIMEOUT - 1
    st._reap_once(time.time())
    assert "extcall1" not in st.calls
    assert any(p.status_line == "SIP/2.0 408 Request Timeout" and a == CALLER
               for p, a in st.sent)
    assert any(p.status_line.startswith("CANCEL") and a == PROXY for p, a in st.sent)
    assert any(p.status_line.startswith("CANCEL") and a == FRITZ for p, a in st.sent)


def test_established_and_fresh_calls_survive_the_reaper(station_factory):
    st = fritz_door_station(station_factory)
    st.on_invite(intercom_invite(), CALLER)
    call = st.calls["extcall1"]
    call.state = "established"
    call.created = time.time() - 9999               # a 3-hour call is legal
    st.on_invite(intercom_invite("extcall2"), CALLER)  # fresh, still ringing
    st._reap_once(time.time())
    assert "extcall1" in st.calls and "extcall2" in st.calls


def test_stale_outbound_call_is_reaped(station_factory):
    st = station_factory("--sip-host", "127.0.0.1", "--sip-port", "5099",
                         "--sip-domain", "sip.linphone.org",
                         "--sip-user", "alice", "--sip-pass", "secret")
    st.registered.set()
    st.call_out("10000000000")
    cid = next(iter(st.out_calls))
    st.out_calls[cid]["ts"] = time.time() - st.RING_TIMEOUT - 1
    st._reap_once(time.time())
    assert not st.out_calls
    assert any(p.status_line.startswith("CANCEL") and a == st.server for p, a in st.sent)


def test_intercom_call_also_rings_the_fritzfon(station_factory):
    """--fritz makes the ONE registered extension behave like a trunk:
    every bridged call races Linphone and the fritzfon - not just door
    calls (no separate intercom contact; the registrar keys devices by
    source IP so a second extension can never coexist anyway)"""
    st = fritz_door_station(station_factory)
    st.on_invite(intercom_invite(), CALLER)
    inv = fritz_invites(st)[0]
    assert inv.status_line == "INVITE sip:1@fritz.box SIP/2.0"
    assert "m=video" not in inv.body
    call = st.calls["extcall1"]
    assert call.trunk is st.provider and call.fritz_leg is not None


def test_fritz_wins_an_intercom_call(station_factory):
    """fritzfon answers an extension call first: the provider leg is
    cancelled and the promoted fritz leg takes ACK/teardown"""
    st = fritz_door_station(station_factory)
    st.on_invite(intercom_invite(), CALLER)
    call = st.calls["extcall1"]
    prov_via = provider_invites(st)[-1].get("Via")
    finv = fritz_invites(st)[0]
    st.on_response(response(finv, "200 OK", body=FRITZ_SDP), FRITZ)
    can = next(p for p, a in st.sent if a == PROXY and p.status_line.startswith("CANCEL"))
    assert can.get("Via") == prov_via
    assert call.trunk is st.fritz and call.state == "established"
    assert call.fritz_leg is None and call.prov_call_id == finv.get("Call-ID")
    assert call.media.peer == ("10.0.0.30", 40000)         # fritz audio peer

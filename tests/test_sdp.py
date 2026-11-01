"""Private hicore SDP - values taken from live captures in this session."""
from metzler.media.g711 import pcm_to_ulaw, ulaw_to_pcm
from metzler.media.rtp import is_rtp
from metzler.media.rtsp import RTSP_SDP_AV
from metzler.sip.sdp import (
    media_addr,
    parse_sdp,
    sdp_answer_firmware,
    sdp_answer_talkback,
)

# .47 (10000000001, a real indoor extension) answering our direct INVITE,
# 2026-11-01 10:44:45 - byte for byte
REAL_47_ANSWER = ("v=0\r\no=Q12345678 0 0 IN IP4 10.0.0.47\r\ns=Talk session\r\n"
                  "c=IN IP4 10.0.0.47\r\nt=0 0\r\n"
                  "a=doorFloor:0\r\na=responseType:0\r\na=doorType:0\r\n"
                  "a=isSpecialType:2\r\na=isConfirm:0\r\n")

# what sip.linphone.org's media relay forwarded (it rewrites c= and adds nortpproxy)
FLEXISIP_RELAYED = ("v=0\r\no=- 0 0 IN IP4 198.51.100.7\r\ns=Talk session\r\n"
                    "c=IN IP4 176.31.149.179\r\nt=0 0\r\na=nortpproxy:yes\r\n"
                    "m=audio 14842 RTP/AVP 0 101\r\na=rtpmap:0 PCMU/8000\r\n"
                    "a=rtpmap:101 telephone-event/8000\r\na=fmtp:101 0-16\r\n")


class Opts:
    special_type = "0"


def test_talkback_answer_is_byte_identical_to_a_real_indoor():
    assert sdp_answer_talkback("Q12345678", "10.0.0.47") == REAL_47_ANSWER


def test_talkback_sdp_has_the_173_byte_size_of_the_native_offer():
    """.47's own extension-call INVITE carried exactly 173 bytes of SDP"""
    assert len(sdp_answer_talkback("Q12345678", "10.0.0.47")) == 173


def test_special_type_default_is_audio_ui_and_overridable():
    """isSpecialType:2 -> caller skips the remote-video rect (audio UI);
    0 drew a video area on .47's screen (confirmed by the user)"""
    assert "a=isSpecialType:2\r\n" in sdp_answer_talkback("Q1", "1.2.3.4")
    assert "a=isSpecialType:0\r\n" in sdp_answer_talkback("Q1", "1.2.3.4", opts=Opts())


def test_header_only_offer_parses_to_no_media():
    sdp = parse_sdp(REAL_47_ANSWER)
    assert sdp["ip"] == "10.0.0.47" and sdp["media"] == []
    assert media_addr(sdp, "audio") is None


def test_relayed_provider_sdp():
    sdp = parse_sdp(FLEXISIP_RELAYED)
    assert media_addr(sdp, "audio") == ("176.31.149.179", 14842)
    assert sdp["media"][0]["formats"] == ["0", "101"]
    assert "rtpmap:0 PCMU/8000" in sdp["media"][0]["attrs"]


def test_per_media_connection_line_wins():
    sdp = parse_sdp("v=0\r\nc=IN IP4 1.1.1.1\r\nm=audio 10 RTP/AVP 0\r\nc=IN IP4 2.2.2.2\r\n"
                      "m=video 20 RTP/AVP 96\r\n")
    assert media_addr(sdp, "audio") == ("2.2.2.2", 10)
    assert media_addr(sdp, "video") == ("1.1.1.1", 20)


def test_door_answer_template_carries_media_lines():
    sdp = parse_sdp(sdp_answer_firmware("10000000003", "10.0.0.5", 40000, 40002))
    assert media_addr(sdp, "audio") == ("10.0.0.5", 40000)
    assert media_addr(sdp, "video") == ("10.0.0.5", 40002)


def test_rtsp_describe_advertises_both_tracks():
    """their RTSP client RSTs on an audio-only DESCRIBE"""
    sdp = parse_sdp(RTSP_SDP_AV)
    assert [x["type"] for x in sdp["media"]] == ["video", "audio"]
    assert "control:trackID=1" in sdp["media"][0]["attrs"]
    assert "control:trackID=2" in sdp["media"][1]["attrs"]


def test_is_rtp_skips_rtcp():
    hdr = lambda pt: bytes([0x80, pt]) + b"\0" * 10
    assert is_rtp(hdr(0) + b"x" * 160)
    assert is_rtp(hdr(0x80 | 96))                              # marker bit set
    assert not is_rtp(hdr(200) + b"\0" * 16)                   # RTCP sender report
    assert not is_rtp(b"\x00" * 20)                            # not RTP version 2


def test_ulaw_round_trip():
    # 0x7F is mu-law "negative zero" and canonicalises to 0xFF
    for code in range(256):
        back = pcm_to_ulaw(ulaw_to_pcm(bytes([code])))[0]
        assert back == (0xFF if code == 0x7F else code)

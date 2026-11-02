"""The intercom media channel: hikvision-style RTSP.

Indoor-to-indoor calls carry no RTP in SIP; after the ACK each station pulls the
other's rtsp://<ip>:554/ch1/main/av_stream. RtspServer is our side of that pull,
RtspPuller pulls the caller's stream.

This module holds the wire format shared by both sides: the DESCRIBE SDP a real
indoor unit serves and the request/reply framing helpers."""

#
# Intercom calls don't use talkback RTP: after ACK each station pulls the other's
# rtsp://<ip>/ch1/main/av_stream (Basic auth admin:<extension password>), which
# carries H264 video (trackID=1) + PCMU mic audio (trackID=2), interleaved TCP.
_RTSP_SDP_HEAD = (
    "v=0\r\no=- 0 0 IN IP4 0.0.0.0\r\ns=HIK Media Server\r\n"
    "i=HIK Media Server Session Description : standard\r\ne=NONE\r\n"
    "c=IN IP4 0.0.0.0\r\nt=0 0\r\na=control:*\r\na=range:npt=0.0-\r\n"
)


_RTSP_SDP_VIDEO = (
    "m=video 0 RTP/AVP 96\r\ni=Video Media\r\na=rtpmap:96 H264/90000\r\n"
    "a=fmtp:96 profile-level-id=4D0014;packetization-mode=0\r\na=control:trackID=1\r\n"
)


_RTSP_SDP_AUDIO = "m=audio 0 RTP/AVP 0\r\ni=Audio Media\r\na=rtpmap:0 PCMU/8000\r\na=control:trackID=2\r\n"


_RTSP_SDP_TAIL = (
    "a=Media_header:MEDIAINFO=494D4B48010100000400000110710110401F000000FA0000"
    "00000000000000000000000000000000000000;\r\na=appversion:1.0\r\n"
)


# a real indoor station's :554 always advertises BOTH tracks in DESCRIBE (the
# hikvision client TCP-RSTs on an audio-only SDP within ~2 ms) but then serves
# ONLY PCMU audio on the wire - indoor->indoor calls carry no video transport.
RTSP_SDP = _RTSP_SDP_HEAD + _RTSP_SDP_AUDIO + _RTSP_SDP_TAIL


RTSP_SDP_AV = _RTSP_SDP_HEAD + _RTSP_SDP_VIDEO + _RTSP_SDP_AUDIO + _RTSP_SDP_TAIL


def rtsp_reply(conn, cseq, code="200 OK", extra="", body=None):
    r = f"RTSP/1.0 {code}\r\nCSeq: {cseq}\r\n{extra}"
    if body is not None:
        r += f"Content-Type: application/sdp\r\nContent-Length: {len(body)}\r\n\r\n{body}"
    else:
        r += "\r\n"
    conn.sendall(r.encode())


def read_rtsp_request(conn, buf):
    """return (request_bytes, rest) skipping $-interleaved RTCP; None on close"""
    while b"\r\n\r\n" not in buf:
        d = conn.recv(8192)
        if not d:
            return None, b""
        buf += d
    i = buf.find(b"\r\n\r\n") + 4
    req, buf = buf[:i], buf[i:]
    while buf[:1] == b"$" and len(buf) >= 4:
        ln = int.from_bytes(buf[2:4], "big")
        if len(buf) < 4 + ln:
            break
        buf = buf[4 + ln :]
    return req, buf


def rtsp_header(lines, name):
    """value of an RTSP header (case-insensitive) or ''"""
    return next((line.split(":", 1)[1].strip() for line in lines if line.lower().startswith(name + ":")), "")

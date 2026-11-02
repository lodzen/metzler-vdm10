"""SDP parsing and the hicore answer templates (private a= attributes)."""

import re
from typing import cast


def parse_sdp(body):
    """-> {ip, media:[{type, port, formats}]}"""
    ip = None
    media = []
    cur = None
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("c=IN IP4 "):
            if cur is not None:
                cur["ip"] = line.split()[-1]
            else:
                ip = line.split()[-1]
        elif line.startswith("m="):
            p = line[2:].split()
            cur = {
                "type": p[0],
                "port": int(p[1]),
                "proto": p[2] if len(p) > 2 else "RTP/AVP",
                "formats": p[3:],
                "ip": ip,
            }
            media.append(cur)
        elif cur is not None and line.startswith("a="):
            cast(list, cur.setdefault("attrs", [])).append(line[2:])
    return {"ip": ip, "media": media}


def media_addr(sdp, mtype):
    """-> (ip, port) of the first media line of `mtype`, or None"""
    m = next((m for m in sdp["media"] if m["type"] == mtype), None)
    return ((m["ip"] or sdp["ip"]), m["port"]) if m else None


def telephone_event_pt(sdp):
    """negotiated telephone-event PT from an answer's audio m-line, or None.
    The answer may remap the offered 101 - and may drop the codec entirely
    (peer then sends DTMF in-band or as SIP INFO)."""
    m = next((m for m in sdp["media"] if m["type"] == "audio"), None)
    if not m:
        return None
    for a in m.get("attrs", []):
        mm = re.match(r"rtpmap:(\d+)\s+telephone-event", a)
        if mm:
            return int(mm.group(1))
    return None


def sdp_answer_firmware(num, ip, audio_port, video_port):
    """the exact template hicore emits (FUN_000dd18c)"""
    return (
        f"v=0\r\no={num} 0 0 IN IP4 {ip}\r\ns=Talk session\r\nc=IN IP4 {ip}\r\nt=0 0\r\n"
        "a=doorFloor:0\r\na=responseType:0\r\na=doorType:0\r\n"
        "a=isSpecialType:0\r\na=isConfirm:0\r\n"
        f"m=audio {audio_port} RTP/AVP 0 2 101\r\na=rtpmap:0 PCMU/8000\r\n"
        "a=rtpmap:2 G726-32/8000\r\na=rtpmap:101 telephone-event/8000\r\n"
        "a=fmtp:101 0-16\r\na=sendrecv\r\n"
        f"m=video {video_port} RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
        "a=fmtp:96 packetization-mode=1;profile-level-id=4D001E\r\na=sendrecv\r\n"
    )


def sdp_answer_talkback(serial, ip, audio_port=None, video_port=None, opts=None):
    """the real 200 OK SDP a hicore station emits when answering an EXTENSION call
    (sess+0x7c == 3 -> FUN_000dd18c header-only path, sip_client.c ~5968/6160).
    Header-only SDP + the same private session attrs as the offer:
      doorFloor:0 responseType:<wait outcome> doorType:0 isSpecialType:<2|0> isConfirm:0
    responseType = FUN_000dc2dc wait outcome: 0 = answered (200); the caller copies
    attr#1 -> sess+0x78 and attr#3 -> sess+0xcc and posts {+0x78,+0x7c,+0xcc} to the
    GUI (msg 0x80f; byte0==4 routes to the videocall window, else the initiate
    window).

    isSpecialType — VERIFIED ON THE WIRE (real indoor 10000000001 @ .47, both
    its offer and its 200 OK send 2): 2 = "no remote video exists" -> the
    caller's videocallProc skips the remote-video rect -> audio-call UI.
    0 -> video rect drawn -> "video session" UI. Real indoor stations claim
    the special-model flag (isSpecialModel -> product-code band, FUN_00064da8).
    Default 2 to match; INDOOR_SPECIAL_TYPE overrides for A/B tests."""
    special = getattr(opts, "special_type", "2")
    return (
        f"v=0\r\no={serial} 0 0 IN IP4 {ip}\r\ns=Talk session\r\nc=IN IP4 {ip}\r\nt=0 0\r\n"
        "a=doorFloor:0\r\na=responseType:0\r\na=doorType:0\r\n"
        f"a=isSpecialType:{special}\r\na=isConfirm:0\r\n"
    )

"""Our :554 must look exactly like a real indoor unit's stream: DESCRIBE
advertises video+audio, the wire carries only PCMU on the audio channel,
320-byte payloads every 40 ms, ssrc as declared in SETUP (pcap of .38/.47)."""

import collections
import itertools
import socket
import struct
import time

from metzler.media.intercom import SipIntercomMedia
from metzler.sip.sdp import parse_sdp


def rtsp(sock, method, url, cseq, extra=""):
    sock.sendall(f"{method} {url} RTSP/1.0\r\nCSeq: {cseq}\r\n{extra}\r\n".encode())
    data = b""
    while b"\r\n\r\n" not in data:
        data += sock.recv(4096)
    head, _, rest = data.partition(b"\r\n\r\n")
    head = head.decode()
    n = int(next((line.split(":")[1] for line in head.split("\r\n") if line.startswith("Content-Length")), 0))
    while len(rest) < n:
        rest += sock.recv(4096)
    return head, rest[:n].decode()


def read_frames(sock, count):
    buf, frames = b"", []
    while len(frames) < count:
        buf += sock.recv(8192)
        while len(buf) >= 4 and buf[0:1] == b"$":
            ln = int.from_bytes(buf[2:4], "big")
            if len(buf) < 4 + ln:
                break
            frames.append((buf[1], buf[4 : 4 + ln]))
            buf = buf[4 + ln :]
    return frames


def test_rtsp_stream_shape(station_factory):
    st = station_factory()
    port = st.rtsp.ls.getsockname()[1]
    st.rtsp.mic = lambda: b"\x55" * 160
    url = f"rtsp://127.0.0.1:{port}/ch1/main/av_stream"
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    head, _ = rtsp(s, "OPTIONS", url, 1)
    assert "DESCRIBE" in head and "SETUP" in head
    head, sdp = rtsp(s, "DESCRIBE", url, 2)
    assert [x["type"] for x in parse_sdp(sdp)["media"]] == ["video", "audio"]
    head, _ = rtsp(s, "SETUP", url + "/trackID=1", 3, "Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n")
    head, _ = rtsp(s, "SETUP", url + "/trackID=2", 4, "Transport: RTP/AVP/TCP;unicast;interleaved=2-3\r\n")
    ssrc = int(head.split("ssrc=")[1].split()[0], 16)
    rtsp(s, "PLAY", url, 5, "Range: npt=0.000-\r\n")
    t0 = time.time()
    frames = read_frames(s, 5)
    elapsed = time.time() - t0
    s.close()
    assert {ch for ch, _ in frames} == {2}  # never a video packet
    for _, pkt in frames:
        assert pkt[1] & 0x7F == 0  # PCMU
        assert len(pkt) - 12 == 320  # 2 x 160 B = 40 ms
        assert struct.unpack(">I", pkt[8:12])[0] == ssrc
    ts = [struct.unpack(">I", p[4:8])[0] for _, p in frames]
    assert all(b - a == 320 for a, b in itertools.pairwise(ts))
    assert elapsed >= 0.12  # paced, ~40 ms each


def test_sip_intercom_media_splits_peer_audio_into_160_byte_frames():
    media = SipIntercomMedia.__new__(SipIntercomMedia)
    media.rxq = collections.deque([b"a" * 160 + b"b" * 160, b"c" * 80])
    media.tx = 0
    assert media.mic_frame() == b"a" * 160
    assert media.mic_frame() == b"b" * 160
    assert media.mic_frame() == b"c" * 80 + b"\xff" * 80  # padded with mu-law silence
    assert media.mic_frame() == b"\xff" * 160  # underrun -> silence


def test_sip_intercom_media_sheds_stale_peer_audio_to_bound_latency():
    """early media fills rxq before the caller's pull runs; oldest-first
    serving would pin that backlog as a constant delay - mic_frame() sheds
    down to ~8 frames so served audio stays near real-time."""
    media = SipIntercomMedia.__new__(SipIntercomMedia)
    media.rxq = collections.deque(bytes([i]) * 160 for i in range(50))
    media.tx = 0
    assert media.mic_frame() == bytes([42]) * 160  # queue shed 50 -> 8, oldest served next
    assert len(media.rxq) == 7


def test_puller_pulls_our_own_server_like_two_stations_do(station_factory):
    """symmetric intercom media: the callee's RtspPuller against a :554 server"""
    import threading
    import types

    from metzler.media.rtsp_puller import RtspPuller

    st = station_factory()
    st.rtsp.mic = lambda: b"\x2a" * 160
    got, done = [], threading.Event()

    def on_audio(payload):
        got.append(payload)
        if len(got) >= 3:
            done.set()

    opts = types.SimpleNamespace(rtsp_port=st.rtsp.ls.getsockname()[1], password="pw")
    puller = RtspPuller(opts, "127.0.0.1", on_audio, None)
    puller.start()
    assert done.wait(3), "no audio pulled"
    puller.stop()
    assert all(p == b"\x2a" * 320 for p in got[:3])
    assert puller.session  # session id learned from SETUP

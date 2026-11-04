"""MJPEG relay: polls the outdoor station's JPEG snapshots only while a door
call is active and somebody watches, throttled to --mjpeg-fps."""

import base64
import http.client
import itertools
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from simulate_call import FAKE_JPEG, ISAPI_PASS, FakeOutdoorStation

from metzler.station.mjpeg import MjpegRelay, parse_box, shrink


@pytest.fixture
def rig():
    """fake outdoor station + relay whose 'active door call' flag the test controls"""
    door = FakeOutdoorStation()
    state = {"call": False}
    o = SimpleNamespace(
        local_ip="127.0.0.1",
        mjpeg_port=0,
        mjpeg_fps=10,
        mjpeg_channel=102,
        mjpeg_user="door",
        mjpeg_pass="",
        door_isapi_user="admin",
        door_isapi_pass=ISAPI_PASS,
        door_isapi_port=door.port,
        password="",
    )
    relay = MjpegRelay(o, lambda: "127.0.0.1" if state["call"] else None)
    relay.start()
    port = relay.httpd.server_port
    yield SimpleNamespace(o=o, door=door, state=state, relay=relay, port=port)
    relay.stop()
    door.shutdown()


def get(rig, path, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", rig.port, timeout=8)
    c.request("GET", path, headers=headers or {})
    return c, c.getresponse()


def hang_up(c, r):
    r.close()
    c.close()


def read_parts(resp, n):
    """first n JPEG parts of a multipart/x-mixed-replace stream"""
    parts = []
    while len(parts) < n:
        head = b""
        while not head.endswith(b"\r\n\r\n"):
            head += resp.fp.read(1)
        size = int(re.search(rb"Content-Length: (\d+)", head).group(1))
        parts.append(resp.fp.read(size))
        resp.fp.read(2)
    return parts


def test_no_door_call_means_503_and_no_polling(rig):
    c, r = get(rig, "/mjpeg")
    assert r.status == 503 and r.read() == b"no active door call"
    c.close()
    time.sleep(0.5)
    assert rig.door.snapshots == []  # the door is never touched without a call


def test_stream_during_a_door_call_is_mjpeg_and_throttled(rig):
    rig.state["call"] = True
    c, r = get(rig, "/mjpeg")
    assert r.status == 200
    assert r.getheader("Content-Type") == "multipart/x-mixed-replace; boundary=mjpegframe"
    t0 = time.monotonic()
    parts = read_parts(r, 6)
    elapsed = time.monotonic() - t0
    c.close()
    assert parts == [FAKE_JPEG] * 6
    assert elapsed >= 0.4  # 6 frames at 10 fps: 5 intervals >= 0.5 s minus jitter
    ts = rig.door.snapshots
    assert all(b - a >= 0.08 for a, b in itertools.pairwise(ts))  # never faster than the throttle


def test_snapshot_endpoint_returns_one_jpeg(rig):
    rig.state["call"] = True
    c, r = get(rig, "/snapshot.jpg")
    assert r.status == 200 and r.getheader("Content-Type") == "image/jpeg"
    assert r.read() == FAKE_JPEG
    c.close()


def test_polling_stops_when_the_viewer_leaves_and_when_the_call_ends(rig):
    rig.state["call"] = True
    c, r = get(rig, "/mjpeg")
    read_parts(r, 2)
    hang_up(c, r)  # viewer gone -> the poller must wind down
    time.sleep(0.6)
    n = len(rig.door.snapshots)
    time.sleep(0.5)
    assert len(rig.door.snapshots) == n and not rig.relay.polling

    c, r = get(rig, "/mjpeg")
    read_parts(r, 2)
    rig.state["call"] = False  # door call over -> the stream ends by itself
    assert r.fp.read() is not None
    c.close()
    time.sleep(0.4)
    assert not rig.relay.polling


def test_unknown_path_is_404(rig):
    rig.state["call"] = True
    c, r = get(rig, "/")
    assert r.status == 404
    c.close()


def test_optional_basic_auth(rig):
    rig.state["call"] = True
    rig.o.mjpeg_pass = "secret"
    c, r = get(rig, "/snapshot.jpg")
    assert r.status == 401 and "Basic" in r.getheader("WWW-Authenticate")
    c.close()
    bad = {"Authorization": "Basic " + base64.b64encode(b"door:nope").decode()}
    c, r = get(rig, "/snapshot.jpg", bad)
    assert r.status == 401
    c.close()
    good = {"Authorization": "Basic " + base64.b64encode(b"door:secret").decode()}
    c, r = get(rig, "/snapshot.jpg", good)
    assert r.status == 200 and r.read() == FAKE_JPEG
    c.close()


def make_jpeg(w, h):
    """a real JPEG with some structure (so scaling has something to do)"""
    import io

    from PIL import Image

    im = Image.new("RGB", (w, h))
    im.putdata([(x * 255 // w, y * 255 // h, (x + y) % 256) for y in range(h) for x in range(w)])
    out = io.BytesIO()
    im.save(out, "JPEG", quality=90)
    return out.getvalue()


def size_of(jpeg):
    import io

    from PIL import Image

    return Image.open(io.BytesIO(jpeg)).size


def test_parse_box():
    assert parse_box("480x640") == (480, 640)
    assert parse_box(" 240 X 320 ") == (240, 320)
    assert [parse_box(x) for x in ("off", "", "0", "0x0", "480", None)] == [None] * 6


def test_shrink_fits_the_fritzfon_box_keeping_the_aspect():
    pytest.importorskip("PIL")
    out = shrink(make_jpeg(704, 576), (480, 640))
    w, h = size_of(out)
    assert w == 480 and abs(h - 576 * 480 / 704) <= 1  # width-limited, aspect kept
    assert size_of(shrink(make_jpeg(704, 576), (240, 320))) == (240, 196)


def test_shrink_never_enlarges_or_recodes_small_frames():
    pytest.importorskip("PIL")
    small = make_jpeg(320, 240)
    assert shrink(small, (480, 640)) is small


def test_relay_serves_shrunk_frames(rig):
    pytest.importorskip("PIL")
    rig.door.jpeg = make_jpeg(704, 576)
    rig.relay.box = parse_box("480x640")
    rig.state["call"] = True
    c, r = get(rig, "/snapshot.jpg")
    body = r.read()
    hang_up(c, r)
    assert r.status == 200 and size_of(body)[0] == 480
    assert len(body) < len(rig.door.jpeg)


def test_relay_without_a_box_serves_the_doors_frame_untouched(rig):
    pytest.importorskip("PIL")
    rig.door.jpeg = make_jpeg(704, 576)
    rig.state["call"] = True
    c, r = get(rig, "/snapshot.jpg")
    body = r.read()
    hang_up(c, r)
    assert body == rig.door.jpeg


def test_relay_falls_back_to_the_original_frame_when_scaling_fails(rig):
    rig.relay.box = (480, 640)  # FAKE_JPEG is not a decodable image
    rig.state["call"] = True
    c, r = get(rig, "/snapshot.jpg")
    body = r.read()
    hang_up(c, r)
    assert r.status == 200 and body == FAKE_JPEG
    assert rig.relay._resize_failed


def test_junk_and_idle_connections_do_not_print_tracebacks(rig, capsys):
    """the main indoor pushes binary visNet data / opens idle sockets on the relay's port"""
    import socket

    for payload in (b"\x00\x01\x20\x33" + bytes(40), b""):
        s = socket.create_connection(("127.0.0.1", rig.port), timeout=3)
        s.sendall(payload)
        s.close()
    time.sleep(0.4)
    c, r = get(rig, "/snapshot.jpg")  # the server still answers afterwards
    assert r.status == 503
    hang_up(c, r)
    assert "Traceback" not in capsys.readouterr().err

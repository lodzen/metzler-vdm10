"""ha_mode (vdm10-ha): parked ringing calls, explicit answer/hangup, event hook.
Outgoing packets are recorded (conftest.station_factory), never sent."""

from metzler.station.ha_media import SILENCE, HaCallMedia, HaIntercomMedia
from test_door_video import door_invite
from test_station import intercom_invite

CALLER = ("127.0.0.1", 5097)


def build_ha(station_factory, **attrs):
    return station_factory("--local-tone", "440", ha_mode=True, **attrs)


def test_ha_mode_parks_the_call_ringing_and_fires_ring(station_factory):
    events = []
    st = build_ha(station_factory, call_events=lambda name, call: events.append((name, call.id, call.door)))
    st.on_invite(door_invite(), CALLER)
    call = st.calls["doorcall1"]
    assert call.state == "early" and call.door and call.kind == "intercom"
    assert events == [("ring", "doorcall1", True)]
    # 100 Trying + 180 Ringing went out, but nothing answered and no provider leg exists
    assert st.sent.methods() == ["100", "180"]
    assert call.local is None and call.media is None and call.trunk is None


def test_ha_mode_never_answers_itself(station_factory):
    st = build_ha(station_factory)
    st.on_invite(intercom_invite(), CALLER)
    assert st.calls["extcall1"].state == "early"
    assert "200" not in st.sent.methods()  # no auto-answer despite local-tone being configured


def test_answer_call_sends_200_and_establishes(station_factory):
    events = []
    st = build_ha(station_factory, call_events=lambda n, c: events.append(n))
    st.on_invite(door_invite(), CALLER)
    assert st.answer_call("doorcall1")
    call = st.calls["doorcall1"]
    assert call.state == "established" and events == ["ring", "answered"]
    ok = st.sent.first("SIP/2.0 200")
    assert ok.get("Content-Type") == "application/sdp" and ok.body
    assert isinstance(call.local, HaIntercomMedia)  # header-only door offer -> rtsp.mic path
    assert st.rtsp.mic == call.local.mic_frame


def test_answer_call_rejects_unknown_or_nonringing(station_factory):
    st = build_ha(station_factory)
    assert st.answer_call("nonexistent") is False
    st.on_invite(door_invite(), CALLER)
    st.answer_call("doorcall1")
    assert st.answer_call("doorcall1") is False  # already established


def test_hangup_declines_ringing_call(station_factory):
    events = []
    st = build_ha(station_factory, call_events=lambda n, c: events.append((n, c.state)))
    st.on_invite(door_invite(), CALLER)
    assert st.hangup_call("doorcall1")
    assert "doorcall1" not in st.calls
    assert st.sent.first("SIP/2.0 487")  # Via has no :5065 -> plain cancel answer
    assert events == [("ring", "early"), ("ended", "early")]


def test_hangup_uses_486_to_clear_the_registrar_busy_flag(station_factory):
    st = build_ha(station_factory)
    inv = door_invite()
    inv.set("Via", "SIP/2.0/UDP 127.0.0.1:5065;branch=z9hG4bKdoor", replace=True)
    st.on_invite(inv, CALLER)
    st.hangup_call("doorcall1")
    assert st.sent.first("SIP/2.0 486")


def test_hangup_byes_an_established_call(station_factory):
    st = build_ha(station_factory)
    st.on_invite(door_invite(), CALLER)
    st.answer_call("doorcall1")
    st.hangup_call("doorcall1")
    assert "doorcall1" not in st.calls
    assert any(p.status_line.startswith("BYE") for p, _ in st.sent)
    assert st.rtsp.mic is None  # media released the shared mic feed


def test_call_events_exceptions_do_not_kill_call_handling(station_factory):
    def boom(name, call):
        raise RuntimeError("listener bug")

    st = build_ha(station_factory, call_events=boom)
    st.on_invite(door_invite(), CALLER)  # hook raises - the call still parks + rings
    assert st.calls["doorcall1"].state == "early"
    st.hangup_call("doorcall1")  # and teardown survives the broken hook too
    assert "doorcall1" not in st.calls


# ------------------------------------------------------------- HaIntercomMedia


def test_intercom_media_feeds_mic_from_the_injected_source(station_factory):
    st = build_ha(station_factory)
    frames = iter([b"\x01" * 160, None, b"\x02" * 160])
    media = HaIntercomMedia(st.rtsp, lambda: next(frames))
    media.start()
    assert media.mic_frame() == b"\x01" * 160
    assert media.mic_frame() == SILENCE  # muted: feed returns None
    assert media.mic_frame() == b"\x02" * 160
    media.stop()
    assert st.rtsp.mic is None


def test_ha_call_media_is_a_queuefed_localbridge(station_factory):
    o = build_ha(station_factory).o
    feed = lambda: b"\x7f" * 160
    media = HaCallMedia(o, ("127.0.0.1", 40000), None, mic_feed=feed)
    assert media._mic_frame() == b"\x7f" * 160
    media.mic_feed = None
    assert media._mic_frame() == SILENCE
    media.stop()


def test_start_threads_spawns_without_main_loop(station_factory):
    """the embedded entrypoint: threads up, no signal handler, no blocking"""
    st = build_ha(station_factory)
    st.start_threads()
    assert st.listener.__name__ == "listener"  # sane callable spawned

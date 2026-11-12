"""ha_mode (vdm10-ha): parked ringing calls, explicit answer/hangup, event hook.
Outgoing packets are recorded (conftest.station_factory), never sent."""

from metzler.station.ha_media import SILENCE, HaCallMedia, HaIntercomMedia
from test_door_video import door_invite
from test_station import intercom_invite, provider_invites, response

CALLER = ("127.0.0.1", 5097)
PROXY = ("127.0.0.1", 5099)


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


# ------------------------------------------------- ha_mode + trunk (the race)
#
# with a provider or fritz trunk configured under ha_mode the call is parked
# for HA *and* INVITEd to the trunk: whichever side 200-OKs first wins, the
# loser gets CANCELed.


def build_ha_trunk(station_factory, **attrs):
    return station_factory(
        "--sip-host",
        "127.0.0.1",
        "--sip-port",
        "5099",
        "--sip-target",
        "6000",
        ha_mode=True,
        **attrs,
    )


def test_ha_mode_with_provider_parks_and_rings_the_trunk(station_factory):
    events = []
    st = build_ha_trunk(station_factory, call_events=lambda n, c: events.append(n))
    st.on_invite(door_invite(), CALLER)
    call = st.calls["doorcall1"]
    # "bridging" = parked for HA + the trunk leg is ringing (the race is on)
    assert call.state == "bridging" and events == ["ring"]
    assert provider_invites(st), "provider leg must ring alongside the parked HA call"
    assert call.trunk is st.provider


def test_ha_answer_cancels_the_racing_trunk_leg(station_factory):
    st = build_ha_trunk(station_factory)
    st.on_invite(door_invite(), CALLER)
    pcid = provider_invites(st)[0].get("Call-ID")
    assert st.answer_call("doorcall1")
    cancels = [p for p, a in st.sent if a == PROXY and p.status_line.startswith("CANCEL")]
    assert cancels and cancels[0].get("Call-ID") == pcid


def test_provider_answer_wins_the_race_and_reports_other(station_factory):
    events = []
    st = build_ha_trunk(station_factory, call_events=lambda n, c: events.append((n, c.state)))
    st.on_invite(door_invite(), CALLER)
    inv = provider_invites(st)[0]
    st.on_response(
        response(
            inv,
            "200 OK",
            "Content-Type: application/sdp",
            "v=0\r\no=- 1 1 IN IP4 10.9.9.9\r\ns=-\r\nc=IN IP4 10.9.9.9\r\n"
            "t=0 0\r\nm=audio 41002 RTP/AVP 0\r\na=rtpmap:0 PCMU/8000\r\n",
        ),
        PROXY,
    )
    call = st.calls["doorcall1"]
    assert call.state == "established" and events[-1] == ("answered", "established")
    # HA lost the race: answering now must not double-200 the door
    assert st.answer_call("doorcall1") is False
    # and the door side got exactly one 200 OK (from provider_answered)
    assert [p.status_line for p, a in st.sent if a == CALLER].count("SIP/2.0 200 OK") == 1


def test_ha_mode_fritz_only_does_not_crash(station_factory):
    """fritz trunk without a provider: the guarded provider invite must skip"""
    st = station_factory(
        "--fritz",
        "--fritz-host",
        "127.0.0.1",
        "--fritz-port",
        "5077",
        "--fritz-user",
        "620",
        "--fritz-pass",
        "pw",
        "--fritz-call-button",
        "1",
        ha_mode=True,
    )
    st.on_invite(door_invite(), CALLER)
    call = st.calls["doorcall1"]
    # fritz legs live in call.fritz_leg and don't flip the door-leg state
    assert call.state == "early" and call.fritz_leg is not None
    fritz_invites = [p for p, a in st.sent if a == ("127.0.0.1", 5077) and p.status_line.startswith("INVITE")]
    assert fritz_invites, "the fritz leg must ring alongside the parked HA call"

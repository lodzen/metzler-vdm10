"""Command line / .env options and the program entry point."""

import argparse
import logging
import os
import subprocess
import sys

from metzler.config import answer_policy, env_bool, env_default, load_env
from metzler.media.rtp import set_rtp_range
from metzler.media.srtp import SRTP_SUITE, srtp_available
from metzler.sip.message import sip_uri
from metzler.station.station import IndoorStation

log = logging.getLogger("indoor")


def main(argv=None):
    env = load_env(os.environ.get("INDOOR_ENV", ".env"))
    ap = build_parser(env)
    o = ap.parse_args(argv)
    if o.list_audio:
        for cmd in (["aplay", "-l"], ["arecord", "-l"], ["aplay", "-L"]):
            try:
                print("$", " ".join(cmd), "\n", subprocess.run(cmd, capture_output=True, text=True, check=False).stdout)
            except FileNotFoundError:
                print(cmd[0], "not installed")
        sys.exit(0)
    set_rtp_range(o.rtp_port_min, o.rtp_port_max)
    logging.basicConfig(
        level=logging.DEBUG if o.debug else logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    finalize_options(o, ap)
    IndoorStation(o).run()


def build_parser(env):
    """CLI options; every default comes from the INDOOR_* keys of `env`"""
    d = lambda k, default=None, cast=None: env_default(env, k, default, cast)

    def fd(key, default=None, cast=None):
        """fritz option default: INDOOR_FRITZ_* first, then the FRITZ_BOX_* naming"""
        v = env.get("INDOOR_FRITZ_" + key.upper()) or env.get("FRITZ_BOX_" + key.upper()) or default
        return cast(v) if cast and v is not None else v

    ap = argparse.ArgumentParser(
        description="Simulated Hikvision/Metzler indoor station -> SIP bridge. "
        "All options can also be set via .env (INDOOR_* keys)."
    )
    ap.add_argument("--server", default=d("server"), help="registrar IP (door station / primary indoor)")
    ap.add_argument("--server-port", type=int, default=d("server_port", 5065, int))
    ap.add_argument("--username", default=d("username"), help="SIP user / extension number e.g. 10000000005")
    ap.add_argument("--password", default=d("password", ""), help="extension SIP password")
    ap.add_argument("--name", default=d("name", "Asterisk"), help="display name / regDevName")
    ap.add_argument("--serial", default=d("serial", "Q12345678"), help="regDevSerial")
    ap.add_argument("--mac", default=d("mac", "02:00:00:00:00:01"), help="regDevMacAddr")
    ap.add_argument("--devlongnum", default=d("devlongnum", "0"), help="device long number for heartbeat XML")
    ap.add_argument("--floor-num", default=d("floor_num", "0"), help="<floorNum> in regXML")
    ap.add_argument(
        "--heartbeat-target",
        default=d("heartbeat_target", ""),
        help="SIP number the MESSAGE heartbeat is sent to (e.g. the primary "
        "station/door number, 10000000001). Default: own username "
        "(server forwards it back to us)",
    )
    ap.add_argument("--heartbeat-interval", type=int, default=d("heartbeat_interval", 120, int))
    ap.add_argument("--local-ip", default=d("local_ip"), help="this host's IP")
    ap.add_argument("--listen-port", type=int, default=d("listen_port", 5060, int))
    ap.add_argument("--expires", type=int, default=d("expires", 900, int))
    ap.add_argument(
        "--sip-host", default=d("sip_host"), help="SIP provider proxy/registrar host (e.g. sip.linphone.org)"
    )
    ap.add_argument("--sip-port", type=int, default=d("sip_port", 5060, int))
    ap.add_argument(
        "--sip-domain",
        default=d("sip_domain", ""),
        help="SIP provider domain for the AOR (default: same as --sip-host)",
    )
    ap.add_argument("--sip-user", default=d("sip_user", ""), help="provider account/username")
    ap.add_argument("--sip-pass", default=d("sip_pass", ""), help="provider account password")
    ap.add_argument(
        "--sip-target",
        default=d("sip_target", ""),
        help="SIP URI to ring on incoming intercom/door call "
        "(default: sip:<user>@<domain> - rings all devices on the account)",
    )
    ap.add_argument(
        "--sip-from",
        default=d("sip_from", ""),
        help="From identity on the provider leg: 'Klingel', user@domain, "
        "'Name <sip:user@host>'; {num} expands to the calling station's "
        "number, {name} to its mapped --sip-names entry (default: num) "
        "- one caller-ID per station "
        "(default: the --sip-user account)",
    )
    ap.add_argument(
        "--sip-names",
        default=d("sip_names", ""),
        help="station number -> display name map for --sip-from {name}, "
        "comma-separated: 10000000001=Oben,10010100000=Klingel",
    )
    ap.add_argument(
        "--inbound-target",
        default=d("inbound_target", ""),
        help="intercom devLongNum to ring when a call arrives from the SIP "
        "app (e.g. 10000000001 = the indoor unit; 'num@ip' for a "
        "direct :5060 call). Default: decline inbound calls with 486",
    )
    ap.add_argument(
        "--sip-srtp",
        action=argparse.BooleanOptionalAction,
        default=d("sip_srtp", "1") not in ("0", "no", "off", "false"),
        help=f"encrypt the provider media leg with SDES-SRTP ({SRTP_SUITE}); --no-sip-srtp offers plain RTP",
    )
    # --- optional second trunk: REGISTER as an IP phone at a Fritz!Box ---
    ap.add_argument(
        "--fritz",
        action="store_true",
        default=env_bool(env, "fritz"),
        help="also REGISTER at a Fritz!Box as one of its IP phones; "
        "inbound fritz calls bridge to --inbound-target like app "
        "calls (needs --fritz-user/--fritz-pass)",
    )
    ap.add_argument(
        "--fritz-host",
        default=fd("host") or fd("ip", "fritz.box"),
        help="Fritz!Box address (env FRITZ_BOX_IP; default fritz.box)",
    )
    ap.add_argument("--fritz-port", type=int, default=fd("port", 5060, int), help="Fritz!Box SIP port")
    ap.add_argument(
        "--fritz-domain",
        default=fd("domain", "fritz.box"),
        help="registrar domain in the fritz REGISTER (default fritz.box)",
    )
    ap.add_argument("--fritz-user", default=fd("user", ""), help="fritz IP-phone username (env FRITZ_BOX_USER)")
    ap.add_argument(
        "--fritz-pass",
        default=fd("password") or fd("pass", ""),
        help="fritz IP-phone password (env FRITZ_BOX_PASSWORD)",
    )
    ap.add_argument(
        "--fritz-call-button",
        dest="fritz_target",
        default=fd("call_button") or fd("target", ""),
        help="number the fritz leg dials on every bridged call, in parallel "
        "with the provider app (env FRITZ_BOX_CALL_BUTTON). For a "
        "Tuersprechanlage account this is the box-configured 'Rufnummer "
        "der Klingeltaste' (e.g. 1 -> sip:1@fritz.box) - the box treats "
        "it as a doorbell press and rings its phones",
    )
    # --- local-audio mode: no provider, talk through local headset ---
    ap.add_argument(
        "--local-audio",
        action="store_true",
        default=env_bool(env, "local_audio"),
        help="door audio -> local speaker (aplay)",
    )
    ap.add_argument(
        "--local-mic", action="store_true", default=env_bool(env, "local_mic"), help="local mic (arecord) -> door audio"
    )
    ap.add_argument(
        "--local-tone",
        type=float,
        default=d("local_tone", None, float),
        help="send N Hz test tone instead of mic (e.g. --local-tone 440)",
    )
    ap.add_argument("--alsa-device", default=d("alsa_device", "default"), help="ALSA device for aplay/arecord")
    ap.add_argument(
        "--video-player",
        action="store_true",
        default=env_bool(env, "video_player"),
        help="pipe door H264 video to ffplay",
    )
    ap.add_argument(
        "--auto-answer",
        nargs="?",
        const="all",
        type=answer_policy,
        default=d("auto_answer", "off"),
        help="off (default) | all | extension (only calls the main indoor places to us). "
        "Answering a doorbell leg takes the call away from the real indoor stations",
    )
    ap.add_argument(
        "--rtsp-port",
        type=int,
        default=d("rtsp_port", 554, int),
        help="TCP port for the intercom RTSP server (stations pull our stream here)",
    )
    ap.add_argument(
        "--rtsp-video",
        action="store_true",
        default=env_bool(env, "rtsp_video"),
        help="actually SEND H264 video packets on :554 (a real indoor only "
        "advertises the track; it never emits video in extension calls)",
    )
    ap.add_argument(
        "--special-type",
        default=d("special_type", "2"),
        help="a=isSpecialType value in the 200 OK (2 = special product band, "
        "0 = generic room station; the caller's GUI renders the "
        "in-call screen from it)",
    )
    ap.add_argument("--door-isapi-user", default=d("door_isapi_user", "admin"), help="outdoor station ISAPI login")
    ap.add_argument(
        "--door-isapi-port", type=int, default=d("door_isapi_port", 80, int), help="outdoor station HTTP port"
    )
    ap.add_argument(
        "--door-isapi-pass",
        default=d("door_isapi_pass", ""),
        help="outdoor station ISAPI password (default: the indoor password, as for the RTSP pull)",
    )
    ap.add_argument(
        "--talkback-port",
        type=int,
        default=d("talkback_port", 9654, int),
        help="fixed talkback RTP port hicore uses when the SDP has no m= lines (RTCP = port+1)",
    )
    ap.add_argument("--rtp-port-min", type=int, default=d("rtp_port_min", 40000, int))
    ap.add_argument(
        "--rtp-port-max",
        type=int,
        default=d("rtp_port_max", 40099, int),
        help="fixed UDP range for RTP (open it in the Hyper-V firewall)",
    )
    ap.add_argument(
        "--answer-delay",
        type=float,
        default=d("answer_delay", 1.0, float),
        help="seconds to wait after 180 Ringing before 200 OK (local mode)",
    )
    ap.add_argument(
        "--call",
        default=d("call", ""),
        help="place an outbound call to this devLongNum (e.g. 10000000001) after registering — mimics _client_call",
    )
    ap.add_argument("--list-audio", action="store_true", help="print ALSA playback/capture devices and exit")
    ap.add_argument("--debug", action="store_true", default=env_bool(env, "debug"))
    return ap


def finalize_options(o, ap):
    """derive provider defaults, check required options, degrade SRTP when
    the optional crypto package is missing (stdlib-only standalone run)"""
    local_mode = bool(o.local_audio or o.local_mic or o.local_tone or o.video_player)
    embedded = local_mode or getattr(o, "ha_mode", False)  # ha_mode: parked calls, no provider leg needed
    o.sip_domain = o.sip_domain or o.sip_host
    if not o.sip_target and o.sip_user and o.sip_domain:
        o.sip_target = f"sip:{o.sip_user}@{o.sip_domain}"
    o.sip_target = sip_uri(o.sip_target, o.sip_domain)
    if o.fritz_target:
        o.fritz_target = sip_uri(o.fritz_target, o.fritz_domain)
    for f in ("server", "username", "local_ip") + (() if embedded else ("sip_host", "sip_target")):
        if getattr(o, f) in (None, ""):
            ap.error(f"--{f.replace('_', '-')} required (or set INDOOR_{f.upper()} in .env)")
    if o.fritz and not (o.fritz_user and o.fritz_pass):
        ap.error("--fritz needs credentials (--fritz-user/--fritz-pass, or FRITZ_BOX_USER/FRITZ_BOX_PASSWORD in .env)")
    if o.sip_srtp and not embedded and not srtp_available():
        log.warning(
            "SRTP requested but the 'cryptography' package is missing - "
            "offering plain RTP (install it: uv add cryptography / pip install cryptography)"
        )
        o.sip_srtp = False

"""Metzler/Hikvision indoor-station simulator and SIP bridge.

Simulates a VTH extension on the private intercom registrar, accepts
intercom/door calls in the firmware's SIP dialect and bridges them to a
standard SIP provider/PBX or the local headset. See docs/architecture.md and
docs/protocol.md; wire details in ANALYSIS.md.

  uv run indoor-station            # or: python -m metzler / __main__.py
      --server 10.0.0.38         # registrar (main indoor :5065)
      --username 10000000003          # extension number (SIP user)
      --password secret               # extension password (iVMS/SIP config)
      --local-ip 10.0.0.96       # this host
      --sip-host sip.linphone.org     # SIP provider proxy/registrar
      --sip-user alice --sip-pass secret
      --sip-target sip:alice@sip.linphone.org

  Asterisk / PBX trunk (no provider REGISTER when --sip-user/--sip-pass are
  empty; a bare extension is expanded to sip:<ext>@<sip-host>):
      --sip-host 192.168.0.20 --sip-target 6000 [--no-sip-srtp]

Stdlib only at module level. SRTP (default on for the provider leg) lazily
uses the optional `cryptography` package; a peer that refuses the SRTP offer
with 488 is re-offered plain RTP.
"""

from metzler.cli import build_parser, finalize_options, main
from metzler.config import answer_policy, env_bool, env_default, load_env
from metzler.media.g711 import alaw_to_pcm, pcm_to_ulaw, ulaw_to_pcm
from metzler.media.intercom import IntercomMedia, SipIntercomMedia
from metzler.media.local import LocalMediaBridge
from metzler.media.rtp import RtpRelay, bind_rtp, is_rtp, set_rtp_range
from metzler.media.rtsp import RTSP_SDP, RTSP_SDP_AV
from metzler.media.rtsp_puller import RtspPuller
from metzler.media.rtsp_server import RtspServer
from metzler.media.srtp import (
    SRTP_SUITE,
    Srtp,
    SrtpSession,
    srtp_available,
)
from metzler.provider import SipProvider
from metzler.sip.digest import digest_response, md5
from metzler.sip.message import UA, Packet, branch, call_id, sip_uri, tag
from metzler.sip.sdp import (
    media_addr,
    parse_sdp,
    sdp_answer_firmware,
    sdp_answer_talkback,
)
from metzler.station import Call, IndoorStation

__all__ = [
    "RTSP_SDP",
    "RTSP_SDP_AV",
    "SRTP_SUITE",
    "UA",
    "Call",
    "IndoorStation",
    "IntercomMedia",
    "LocalMediaBridge",
    "Packet",
    "RtpRelay",
    "RtspPuller",
    "RtspServer",
    "SipIntercomMedia",
    "SipProvider",
    "Srtp",
    "SrtpSession",
    "alaw_to_pcm",
    "answer_policy",
    "bind_rtp",
    "branch",
    "build_parser",
    "call_id",
    "digest_response",
    "env_bool",
    "env_default",
    "finalize_options",
    "is_rtp",
    "load_env",
    "main",
    "md5",
    "media_addr",
    "parse_sdp",
    "pcm_to_ulaw",
    "sdp_answer_firmware",
    "sdp_answer_talkback",
    "set_rtp_range",
    "sip_uri",
    "srtp_available",
    "tag",
    "ulaw_to_pcm",
]

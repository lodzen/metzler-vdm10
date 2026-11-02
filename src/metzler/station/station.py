"""The simulated indoor station, assembled from one mixin per concern."""

from metzler.station.bridge import ProviderBridgeMixin
from metzler.station.core import StationCore
from metzler.station.inbound import InboundCallMixin
from metzler.station.local_answer import LocalAnswerMixin
from metzler.station.outbound import OutboundCallMixin
from metzler.station.registration import RegistrationMixin
from metzler.station.teardown import TeardownMixin


class IndoorStation(
    RegistrationMixin,
    ProviderBridgeMixin,
    LocalAnswerMixin,
    OutboundCallMixin,
    InboundCallMixin,
    TeardownMixin,
    StationCore,
):
    """A Hikvision/Metzler indoor extension (VTH) on the intercom LAN.

    StationCore           socket, RTSP server, receive loop, request routing, run/shutdown
    RegistrationMixin     REGISTER + <regXML> at the registrar, MESSAGE heartbeat
    ProviderBridgeMixin   incoming intercom/door INVITE -> SIP provider leg
    LocalAnswerMixin      local headset mode (answer on this machine)
    OutboundCallMixin     --call: dial another station (direct or via registrar)
    InboundCallMixin      inbound SIP app call -> --inbound-target on the intercom
    TeardownMixin         CANCEL/BYE from either leg
    """

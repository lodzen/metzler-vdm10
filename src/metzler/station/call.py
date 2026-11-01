"""State of one bridged call (intercom/door leg + provider leg)."""

from metzler.sip.message import call_id, tag


class Call:
    def __init__(self, station, invite, addr):
        self.st = station
        self.invite = invite            # parsed incoming INVITE packet
        self.addr = addr                # door-side SIP addr
        self.id = invite.get("Call-ID")
        self.to_tag = tag()
        self.state = "early"            # early -> bridging -> established -> ended
        self.audio = self.video = None  # RtpRelay pair (door calls, created lazily)
        self.media = None               # SipIntercomMedia (intercom calls)
        self.local = None               # IntercomMedia/LocalMediaBridge (local mode)
        self.kind = None                # "door" | "intercom"
        self.prov_call_id = call_id()
        self.prov_from = None           # our From on the provider leg
        self.prov_to = None             # remote To (with tag) from 200 OK
        self.prov_uri = None            # remote Contact URI (route target)
        self.prov_peer = None           # provider RTP (ip, port) for audio
        self.prov_auths = 0             # digest retries on the provider leg
        self.prov_branch = None         # Via branch of the current INVITE txn
        self.prov_cseq = 0              # CSeq of the current INVITE (for ACKs)
        self.prov_plain = False         # True after a 488 -> re-offered without SRTP

    def stop(self):
        self.state = "ended"
        if self.audio: self.audio.stop()
        if self.video: self.video.stop()
        if self.media: self.media.stop()
        if self.local: self.local.stop()

"""RTP plumbing: port allocation inside the firewall range, RTP/RTCP detection and
the two-sided UDP relay used for door calls."""

import random
import select
import socket
import threading

RTP_RANGE = None   # (lo, hi) from --rtp-port-min/max: a fixed range so one firewall rule covers all RTP


def set_rtp_range(lo, hi):
    global RTP_RANGE
    RTP_RANGE = (lo, hi)


def bind_rtp(sock):
    """bind an RTP socket inside the configured port range (any port if unset)"""
    if not RTP_RANGE:
        return sock.bind(("0.0.0.0", 0))
    ports = list(range(RTP_RANGE[0], RTP_RANGE[1] + 1))
    random.shuffle(ports)
    for p in ports:
        try: return sock.bind(("0.0.0.0", p))
        except OSError: continue
    raise OSError(f"no free RTP port in {RTP_RANGE[0]}-{RTP_RANGE[1]}")


def is_rtp(pkt):
    """RTP (not RTCP: PT 72..76 once the marker bit is masked off)"""
    return len(pkt) >= 12 and (pkt[0] >> 6) == 2 and not 72 <= (pkt[1] & 0x7F) <= 76


class RtpRelay(threading.Thread):
    """Bidirectional UDP relay between door-side and provider-side endpoints."""
    def __init__(self):
        super().__init__(daemon=True)
        self.sock_a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # door side
        self.sock_b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # pbx side
        bind_rtp(self.sock_a); bind_rtp(self.sock_b)
        self.port_a = self.sock_a.getsockname()[1]
        self.port_b = self.sock_b.getsockname()[1]
        self.peer_a = None; self.peer_b = None
        self.srtp = None                # SrtpSession on the provider (b) side
        self.running = True

    def set_peer(self, a=None, b=None):
        if a: self.peer_a = a
        if b: self.peer_b = b

    def _to_b(self, data):
        if self.srtp and is_rtp(data): data = self.srtp.tx.protect(data)
        if self.peer_b: self.sock_b.sendto(data, self.peer_b)

    def _to_a(self, data):
        if self.srtp and self.srtp.rx and is_rtp(data):
            data = self.srtp.rx.unprotect(data)
            if data is None: return                         # bad auth tag
        if self.peer_a: self.sock_a.sendto(data, self.peer_a)

    def run(self):
        socks = [self.sock_a, self.sock_b]
        while self.running:
            r, _, _ = select.select(socks, [], [], 0.5)
            for s in r:
                try: data, addr = s.recvfrom(65535)
                except OSError: return
                self._on_datagram(s, data, addr)

    def _on_datagram(self, sock, data, addr):
        """forward to the other side; the first sender on a side becomes its
        peer (symmetric RTP, covers NAT'ed providers)"""
        if sock is self.sock_a:
            if self.peer_a is None: self.peer_a = addr
            self._to_b(data)
        else:
            if self.peer_b is None: self.peer_b = addr
            self._to_a(data)

    def stop(self):
        self.running = False
        for s in (self.sock_a, self.sock_b):
            try: s.close()
            except OSError: pass

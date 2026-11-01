"""SDES-SRTP (RFC 3711, AES_CM_128_HMAC_SHA1_80) for the provider media leg.

AES comes from the optional `cryptography` package; it is imported lazily so the
simulator still runs on a bare Python install (plain RTP then)."""

import base64
import hashlib
import hmac
import os
import re
import struct

SRTP_SUITE = "AES_CM_128_HMAC_SHA1_80"


def srtp_available():
    """SRTP needs AES from the optional 'cryptography' package; everything
    else in this script is stdlib, so it still runs standalone without it"""
    try:
        import cryptography.hazmat.primitives.ciphers  # noqa: F401
        return True
    except ImportError:
        return False


def _aes_ctr(key, iv, data):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    return Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor().update(data)


class Srtp:
    """One direction of an RFC 3711 SRTP stream, suite AES_CM_128_HMAC_SHA1_80
    (the SDES default Linphone accepts). Key-derivation rate 0, no MKI.
    protect(): RTP -> SRTP for sending; unprotect(): SRTP -> RTP or None if
    the auth tag fails. RTCP is not protected (the bridge sends none)."""
    TAG = 10

    def __init__(self, master_key, master_salt):
        prf = lambda label, n: _aes_ctr(master_key, self._kdf_iv(master_salt, label), b"\0" * n)
        self.k_e, self.k_a, self.k_s = prf(0, 16), prf(1, 20), prf(2, 14)
        self.roc = 0
        self.last_seq = None

    @staticmethod
    def _kdf_iv(salt, label):
        x = int.from_bytes(salt, "big") ^ (label << 48)
        return (x << 16).to_bytes(16, "big")

    @classmethod
    def from_inline(cls, b64):
        raw = base64.b64decode(b64)
        return cls(raw[:16], raw[16:30])

    @staticmethod
    def _hdr_len(pkt):
        n = 12 + 4 * (pkt[0] & 0x0F)
        if pkt[0] & 0x10:                                   # header extension
            n += 4 + 4 * struct.unpack(">H", pkt[n + 2:n + 4])[0]
        return n

    def _iv(self, ssrc, index):
        x = (int.from_bytes(self.k_s, "big") << 16) ^ (ssrc << 64) ^ (index << 16)
        return x.to_bytes(16, "big")

    def _tag(self, authed, roc):
        return hmac.new(self.k_a, authed + struct.pack(">I", roc), hashlib.sha1).digest()[:self.TAG]

    def protect(self, pkt):
        seq, ssrc = struct.unpack(">H", pkt[2:4])[0], struct.unpack(">I", pkt[8:12])[0]
        if self.last_seq is not None and seq < self.last_seq and self.last_seq - seq > 0x8000:
            self.roc = (self.roc + 1) & 0xFFFFFFFF          # sequence wrapped
        self.last_seq = seq
        h = self._hdr_len(pkt)
        enc = pkt[:h] + _aes_ctr(self.k_e, self._iv(ssrc, (self.roc << 16) | seq), pkt[h:])
        return enc + self._tag(enc, self.roc)

    def _guess_roc(self, seq):
        """RFC 3711 appendix A index estimate"""
        if self.last_seq is None: return self.roc
        if self.last_seq < 0x8000:
            return self.roc - 1 if seq - self.last_seq > 0x8000 and self.roc else self.roc
        return self.roc + 1 if self.last_seq - 0x8000 > seq else self.roc

    def unprotect(self, pkt):
        if len(pkt) < 12 + self.TAG: return None
        seq, ssrc = struct.unpack(">H", pkt[2:4])[0], struct.unpack(">I", pkt[8:12])[0]
        body, tag = pkt[:-self.TAG], pkt[-self.TAG:]
        roc = self._guess_roc(seq)
        if not hmac.compare_digest(self._tag(body, roc), tag): return None
        if roc > self.roc or (roc == self.roc and (self.last_seq is None or seq > self.last_seq)):
            self.roc, self.last_seq = roc, seq
        h = self._hdr_len(body)
        return body[:h] + _aes_ctr(self.k_e, self._iv(ssrc, (roc << 16) | seq), body[h:])


class SrtpSession:
    """both directions of one media stream: tx from our offered key, rx from
    the key in the peer's answer (a=crypto). `inline` is our SDP key."""
    def __init__(self):
        raw = os.urandom(30)
        self.inline = base64.b64encode(raw).decode()
        self.tx = Srtp(raw[:16], raw[16:])
        self.rx = None

    def crypto_attr(self):
        return f"a=crypto:1 {SRTP_SUITE} inline:{self.inline}\r\n"

    def accept_answer(self, media):
        """bind the rx key from the answered m= line; False if the peer
        answered without SRTP (plain RTP fallback)"""
        for a in media.get("attrs", []):
            m = re.match(r"crypto:\d+ (\S+) inline:([A-Za-z0-9+/=]+)", a)
            if m and m.group(1) == SRTP_SUITE:
                self.rx = Srtp.from_inline(m.group(2))
                return True
        return False

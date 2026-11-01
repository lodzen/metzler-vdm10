"""G.711 A-law / mu-law codec tables (stdlib only)."""

import struct


def _alaw_decode_table():
    t = []
    for i in range(256):
        a = i ^ 0x55
        v = (a & 0x0F) << 4
        seg = (a & 0x70) >> 4
        v = v + 8 if seg == 0 else (v + 0x108) << (seg - 1) if seg > 1 else v + 0x108
        t.append(v if a & 0x80 else -v)
    return t


ALAW_DEC = _alaw_decode_table()


def alaw_to_pcm(alaw):
    return b"".join(struct.pack("<h", ALAW_DEC[b]) for b in alaw)


def _ulaw_decode_table():
    t = [0] * 256
    for i in range(256):
        u = ~i & 0xFF
        s = (((u & 0x0F) << 3) + 0x84) << ((u >> 4) & 7)
        s -= 0x84
        t[i] = -s if (u & 0x80) else s
    return t


ULAW_DEC = _ulaw_decode_table()


def ulaw_encode_sample(s):
    if s < 0: sign, s = 0x80, -s
    else: sign = 0
    s = min(s, 32635)
    s += 0x84
    exp, mask = 7, 0x4000
    while exp > 0 and not (s & mask):
        mask >>= 1; exp -= 1
    return ~(sign | (exp << 4) | ((s >> (exp + 3)) & 0x0F)) & 0xFF


ULAW_ENC = bytes(ulaw_encode_sample(i - 65536 if i >= 32768 else i) for i in range(65536))


def ulaw_to_pcm(ulaw):
    return b"".join(struct.pack("<h", ULAW_DEC[b]) for b in ulaw)


def pcm_to_ulaw(pcm):
    return bytes(ULAW_ENC[w & 0xFFFF] for w, in struct.iter_unpack("<h", pcm))

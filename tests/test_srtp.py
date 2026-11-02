"""SDES-SRTP AES_CM_128_HMAC_SHA1_80. Linphone Android refused every plain
RTP offer with 488 and accepted RTP/SAVP + a=crypto (probe, 2026-11-01)."""

import struct

import pytest

from metzler.media.srtp import SRTP_SUITE, Srtp, SrtpSession, srtp_available
from metzler.sip.sdp import parse_sdp

pytestmark = pytest.mark.skipif(not srtp_available(), reason="cryptography not installed")

MASTER_KEY = bytes.fromhex("E1F97A0D3E018BE0D64FA32C06DE4139")
MASTER_SALT = bytes.fromhex("0EC675AD498AFEEBB6960B3AABE6")


def rtp(seq, ssrc=7, payload=b"x" * 160, pt=0):
    return struct.pack(">BBHII", 0x80, pt, seq & 0xFFFF, seq * 160 & 0xFFFFFFFF, ssrc) + payload


def test_rfc3711_b3_key_derivation():
    s = Srtp(MASTER_KEY, MASTER_SALT)
    assert s.k_e.hex() == "c61e7a93744f39ee10734afe3ff7a087"
    assert s.k_s.hex() == "30cbbc08863d8c85d49db34a9ae1"
    assert s.k_a.hex() == "cebe321f6ff7716b6fd4ab49af256a156d38baa4"


def test_libsrtp_reference_packet():
    plain = bytes.fromhex("800f1234decafbadcafebabe" + "ab" * 16)
    enc = Srtp(MASTER_KEY, MASTER_SALT).protect(plain)
    assert enc.hex() == ("800f1234decafbadcafebabe4e55dc4ce79978d88ca4d215949d2402b78d6acc99ea179b8dbb")
    assert Srtp(MASTER_KEY, MASTER_SALT).unprotect(enc) == plain


def test_tampered_packet_is_rejected():
    enc = Srtp(MASTER_KEY, MASTER_SALT).protect(rtp(1))
    bad = enc[:20] + bytes([enc[20] ^ 1]) + enc[21:]
    assert Srtp(MASTER_KEY, MASTER_SALT).unprotect(bad) is None
    assert Srtp(MASTER_KEY, MASTER_SALT).unprotect(enc[:15]) is None


def test_sequence_wrap_rolls_over_counter():
    tx, rx = Srtp(MASTER_KEY, MASTER_SALT), Srtp(MASTER_KEY, MASTER_SALT)
    for seq in range(65530, 65545):
        assert rx.unprotect(tx.protect(rtp(seq))) == rtp(seq)
    assert tx.roc == rx.roc == 1


def test_session_crypto_line_and_answer_binding():
    ours, theirs = SrtpSession(), SrtpSession()
    line = ours.crypto_attr()
    assert line.startswith(f"a=crypto:1 {SRTP_SUITE} inline:") and line.endswith("\r\n")
    answer = parse_sdp(f"v=0\r\nc=IN IP4 1.2.3.4\r\nm=audio 4000 RTP/SAVP 0\r\n{theirs.crypto_attr()}")
    assert ours.accept_answer(answer["media"][0])
    # what the peer protects with its key, we can read with the bound rx key
    assert ours.rx and ours.rx.unprotect(theirs.tx.protect(rtp(5))) == rtp(5)


def test_plain_answer_means_no_srtp():
    s = SrtpSession()
    plain = parse_sdp("v=0\r\nc=IN IP4 1.2.3.4\r\nm=audio 4000 RTP/AVP 0\r\na=rtpmap:0 PCMU/8000\r\n")
    assert not s.accept_answer(plain["media"][0])

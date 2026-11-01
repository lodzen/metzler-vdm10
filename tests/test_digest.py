"""SIP Digest auth - the registrar (.38:5065) uses MD5, sip.linphone.org
demands SHA-256 with qop=auth and sends algorithm= UNQUOTED (that bug made
every provider REGISTER fall back to MD5 and end in 403)."""
import hashlib
import re

from metzler.sip.digest import digest_response

# the exact challenge flexisip sent on 2026-11-01 (captured on the wire)
LINPHONE_CHALLENGE = ('Digest realm="sip.linphone.org", nonce="S4uR7gAAAAA0zf+/AAAt4vD7FUEAAAAA", '
                      'opaque="+GNywA==", algorithm=SHA-256, qop="auth"')


def fields(auth):
    return dict(re.findall(r'(\w+)="?([^",]*)"?', auth[len("Digest "):]))


def expected(H, user, realm, pw, method, uri, nonce, nc=None, cnonce=None, qop=None):
    ha1, ha2 = H(f"{user}:{realm}:{pw}"), H(f"{method}:{uri}")
    if qop:
        return H(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
    return H(f"{ha1}:{nonce}:{ha2}")


md5 = lambda s: hashlib.md5(s.encode()).hexdigest()
sha256 = lambda s: hashlib.sha256(s.encode()).hexdigest()


def test_rfc2617_md5_qop_auth_vector():
    """RFC 2617 sec 3.5 example (Mufasa / Circle Of Life)"""
    hdr = ('Digest realm="testrealm@host.com", qop="auth,auth-int", '
           'nonce="dcd98b7102dd2f0e8b11d0f600bfb0c093", opaque="5ccc069c403ebaf9f0171e9517f40e41"')
    f = fields(digest_response(hdr, "GET", "/dir/index.html", "Mufasa", "Circle Of Life"))
    assert f["qop"] == "auth" and f["algorithm"] == "MD5"
    assert f["opaque"] == "5ccc069c403ebaf9f0171e9517f40e41"
    assert f["response"] == expected(md5, "Mufasa", "testrealm@host.com", "Circle Of Life",
                                     "GET", "/dir/index.html", f["nonce"], f["nc"], f["cnonce"], "auth")
    # with the RFC's own cnonce the formula yields the RFC's published response
    assert expected(md5, "Mufasa", "testrealm@host.com", "Circle Of Life", "GET", "/dir/index.html",
                    "dcd98b7102dd2f0e8b11d0f600bfb0c093", "00000001", "0a4f113b",
                    "auth") == "6629fae49393a05397450978507c4ef1"


def test_md5_without_qop_like_the_intercom_registrar():
    hdr = 'Digest realm="TIGERYIN", nonce="abc123"'
    f = fields(digest_response(hdr, "REGISTER", "sip:10.0.0.38:5065", "10000000003", "pw"))
    assert "qop" not in f and "cnonce" not in f
    assert f["response"] == expected(md5, "10000000003", "TIGERYIN", "pw", "REGISTER",
                                     "sip:10.0.0.38:5065", "abc123")


def test_linphone_unquoted_sha256_is_honoured():
    auth = digest_response(LINPHONE_CHALLENGE, "REGISTER", "sip:sip.linphone.org", "alice", "pw")
    f = fields(auth)
    assert f["algorithm"] == "SHA-256"
    assert len(f["response"]) == 64
    assert f["response"] == expected(sha256, "alice", "sip.linphone.org", "pw", "REGISTER",
                                     "sip:sip.linphone.org", "S4uR7gAAAAA0zf+/AAAt4vD7FUEAAAAA",
                                     f["nc"], f["cnonce"], "auth")
    assert f["opaque"] == "+GNywA=="


def test_rfc7616_sha256_vector():
    """RFC 7616 sec 3.9.1 SHA-256 example"""
    hdr = ('Digest realm="http-auth@example.org", qop="auth, auth-int", algorithm=SHA-256, '
           'nonce="7ypf/xlj9XXwfDPEoM4URrv/xwf94BcCAzFZH4GiTo0v", '
           'opaque="FQhe/qaU925kfnzjCev0ciny7QMkPqMAFRtzCUYo5tdS"')
    assert expected(sha256, "Mufasa", "http-auth@example.org", "Circle of Life", "GET", "/dir/index.html",
                    "7ypf/xlj9XXwfDPEoM4URrv/xwf94BcCAzFZH4GiTo0v", "00000001",
                    "f2/wE4q74E6zIJEtWaHKaf5wv/H5QzzpXusqGemxURZJ",
                    "auth") == "753927fa0e85d155564e2e272a28d1802ca10daf4496794697cf8db5856cb6c1"
    f = fields(digest_response(hdr, "GET", "/dir/index.html", "Mufasa", "Circle of Life"))
    assert f["response"] == expected(sha256, "Mufasa", "http-auth@example.org", "Circle of Life", "GET",
                                     "/dir/index.html", f["nonce"], f["nc"], f["cnonce"], "auth")

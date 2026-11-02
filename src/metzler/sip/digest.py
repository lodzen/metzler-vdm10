"""SIP Digest authentication (RFC 2617/7616/8760: MD5 and SHA-256, qop=auth)."""

import hashlib
import random
import re


def md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def digest_response(auth_hdr, method, uri, username, password):
    """RFC 2617/8760 digest - honors the server's algorithm= (MD5 or SHA-256).
    Params may be quoted or bare (linphone sends algorithm=SHA-256 unquoted)."""

    def f(n):
        m = re.search(n + r'="(.*?)"', auth_hdr) or re.search(n + r"=([^,\s]+)", auth_hdr)
        return m.group(1) if m else ""

    realm, nonce, opaque = f("realm"), f("nonce"), f("opaque")
    algo = (f("algorithm") or "MD5").upper()
    H = (lambda s: hashlib.sha256(s.encode()).hexdigest()) if algo.startswith("SHA-256") else md5
    ha1 = H(f"{username}:{realm}:{password}")
    if algo.endswith("-SESS"):
        c0 = f"{random.getrandbits(64):016x}"
        ha1 = H(f"{ha1}:{nonce}:{c0}")
    ha2 = H(f"{method}:{uri}")
    if "qop=" in auth_hdr:
        qop = f("qop").split(",")[0].strip() or "auth"
        nc, cnonce = "00000001", f"{random.getrandbits(128):032x}"
        resp = H(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
        out = (
            f'Digest username="{username}",realm="{realm}",nonce="{nonce}",'
            f'uri="{uri}",response="{resp}",cnonce="{cnonce}",nc={nc},'
            f"qop={qop},algorithm={algo}"
        )
    else:
        resp = H(f"{ha1}:{nonce}:{ha2}")
        out = (
            f'Digest username="{username}",realm="{realm}",nonce="{nonce}",'
            f'uri="{uri}",response="{resp}",algorithm={algo}'
        )
    if opaque:
        out += f',opaque="{opaque}"'
    return out

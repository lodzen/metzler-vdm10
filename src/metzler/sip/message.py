"""SIP message model: header list + body, and the random ids/tags/branches."""

import random
import re

UA = "eXosip/3.6.0"


class Packet(list):
    def __init__(self):
        super().__init__()
        self.status_line = ""
        self.body = ""

    def set(self, k, v, replace=False):
        if replace:
            for i, (kk, _) in enumerate(self):
                if kk == k:
                    self[i] = (k, v); return
        self.append((k, v))

    def get(self, k):
        for kk, v in self:
            if kk.lower() == k.lower(): return v
        return None

    def get_all(self, k):
        return [v for kk, v in self if kk.lower() == k.lower()]

    @staticmethod
    def parse(data):
        p = Packet()
        lines = data.replace("\r\n", "\n").split("\n")
        p.status_line = lines[0].strip()
        i = 1
        for i, line in enumerate(lines[1:], 1):
            if not line.strip(): break
            if ":" in line:
                k, v = line.split(":", 1)
                p.set(k.strip(), v.strip())
        p.body = "\n".join(lines[i + 1:])
        return p

    def __str__(self):
        out = self.status_line + "\r\n"
        out += "".join(f"{k}: {v}\r\n" for k, v in self)
        return out + "\r\n" + self.body


def sip_uri(target, domain):
    """'6000' (Asterisk extension) or 'user@host' -> full sip: URI"""
    if not target or target.startswith(("sip:", "sips:")): return target
    return f"sip:{target}" if "@" in target else f"sip:{target}@{domain}"


def uri_of(header):
    """'"Name" <sip:x@y>;tag=1' (Contact/To/From value) -> 'sip:x@y'"""
    return (re.findall(r"<(.*?)>", header) or [header.strip()])[0]


def userpart(header):
    """'"Name" <sip:10000000003@1.2.3.4>;tag=x' -> '10000000003'"""
    uri = uri_of(header).split(":", 1)
    uri = uri[1] if len(uri) > 1 else uri[0]
    return uri.split("@")[0].split(";")[0]

def call_id(): return "".join(random.choice("0123456789") for _ in range(10))


def tag(): return f"{random.getrandbits(32):08x}"


def branch(): return f"z9hG4bK{random.getrandbits(32):08x}"

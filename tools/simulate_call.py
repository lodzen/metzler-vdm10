#!/usr/bin/env python3
"""Offline end-to-end call simulation - no doorbell, no Linphone, no real network.

Starts a real simulator process in generic-trunk mode on localhost and plays
both ends of a bridged call:

  fake door station --INVITE--> [sim] --INVITE--> fake phone (PBX/Linphone)
                                 ^ phone answers (200 OK, telephone-event negotiated)
                                 ^ phone "presses" digits as RFC 4733 and/or SIP INFO
  fake outdoor station <--PUT /ISAPI/AccessControl/RemoteControl/door/<n>-- [sim]  (the unlock)

    uv run python tools/simulate_call.py                    # '#1' via both transports
    uv run python tools/simulate_call.py --dtmf rfc4733     # or: info | both | none
    uv run python tools/simulate_call.py --digits "#2"      # door 2
    uv run python tools/simulate_call.py --digits "5"       # unrelated key: no unlock
    uv run python tools/simulate_call.py --log              # dump the sim log

Exit code 0 = observed behaviour matched the expectation.
"""

import argparse
import hashlib
import os
import re
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from metzler.sip.message import Packet  # noqa: E402

LOCAL = "127.0.0.1"
DTMF_PT = 101
EVENTS = "0123456789*#"


ISAPI_USER, ISAPI_PASS = "admin", "pw"


class FakeOutdoorStation(HTTPServer):
    """digest-protected fake outdoor station: records every unlock PUT as
    (door, body, digest_ok) in `.unlocks`"""

    def __init__(self):
        super().__init__((LOCAL, 0), _OutdoorHandler)
        self.unlocks = []
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def port(self):
        return self.server_port


class _OutdoorHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, code, body=b"", extra=()):
        self.send_response(code)
        for k, v in extra:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _digest_ok(self, method):
        """None (after sending a 401 challenge) when no Authorization header,
        else whether the digest response is correct"""
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Digest "):
            challenge = 'Digest qop="auth", realm="R", nonce="n0nce", opaque=""'
            self._reply(401, extra=[("WWW-Authenticate", challenge)])
            return None
        f = {k.strip(): v.strip().strip('"') for k, v in (x.split("=", 1) for x in auth[7:].split(","))}
        md5 = lambda t: hashlib.md5(t.encode()).hexdigest()  # noqa: E731
        ha1, ha2 = md5(f"{ISAPI_USER}:R:{ISAPI_PASS}"), md5(f"{method}:{f['uri']}")
        return f["response"] == md5(f"{ha1}:{f['nonce']}:{f['nc']}:{f['cnonce']}:auth:{ha2}")

    def do_PUT(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        good = self._digest_ok("PUT")
        if good is None:
            return
        m = re.fullmatch(r"/ISAPI/AccessControl/RemoteControl/door/(\d+)", self.path)
        self.server.unlocks.append((int(m.group(1)) if m else None, body, good))
        self._reply(200, b"<ResponseStatus><statusCode>1</statusCode></ResponseStatus>")


def udp_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((LOCAL, 0))
    return s


def recv(sock, start, timeout=5.0):
    """next SIP packet whose status line starts with `start`, else None"""
    end = time.time() + timeout
    while (left := end - time.time()) > 0:
        sock.settimeout(left)
        try:
            data, addr = sock.recvfrom(65535)
        except TimeoutError:
            return None, None
        p = Packet.parse(data.decode(errors="replace"))
        if p.status_line.startswith(start):
            return p, addr
    return None, None


def response(req, code, body="", to_tag="phone1"):
    p = Packet()
    p.status_line = f"SIP/2.0 {code}"
    for k in ("Via", "From", "Call-ID", "CSeq"):
        p.set(k, req.get(k))
    p.set("To", req.get("To") + (f";tag={to_tag}" if "tag=" not in req.get("To") else ""))
    p.set("Contact", f"<sip:phone@{LOCAL}>")
    if body:
        p.set("Content-Type", "application/sdp")
    p.body = body
    p.set("Content-Length", str(len(body)))
    return str(p).encode()


def door_invite(door_port, sim_port, kind):
    p = Packet()
    p.status_line = f"INVITE sip:10000000009@{LOCAL}:{sim_port} SIP/2.0"
    p.set("Via", f"SIP/2.0/UDP {LOCAL}:{door_port};branch=z9hG4bKsim1")
    caller = "10010100000" if kind == "door" else "10000000000"
    p.set("From", f"<sip:{caller}@{LOCAL}>;tag=door1")
    p.set("To", f"<sip:10000000009@{LOCAL}:{sim_port}>")
    p.set("Call-ID", "simcall1")
    p.set("CSeq", "20 INVITE")
    p.set("Contact", f"<sip:{caller}@{LOCAL}:{door_port}>")
    p.set("Subject", "This is a call to app." if kind == "door" else "This is a call to extension.")
    p.set("Content-Type", "application/sdp")
    p.body = (
        f"v=0\r\no=Q1 0 0 IN IP4 {LOCAL}\r\ns=Talk session\r\nc=IN IP4 {LOCAL}\r\nt=0 0\r\n"
        f"a=doorFloor:0\r\na=responseType:0\r\na=doorType:{1 if kind == 'door' else 0}\r\n"
        "a=isSpecialType:2\r\na=isConfirm:0\r\n"
    )
    p.set("Content-Length", str(len(p.body)))
    return p


def phone_answer_sdp(rtp_port, telephone_event):
    te = f"a=rtpmap:{DTMF_PT} telephone-event/8000\r\n" if telephone_event else ""
    fmts = f"0 {DTMF_PT}" if telephone_event else "0"
    return (
        f"v=0\r\no=- 1 1 IN IP4 {LOCAL}\r\ns=-\r\nc=IN IP4 {LOCAL}\r\nt=0 0\r\n"
        f"m=audio {rtp_port} RTP/AVP {fmts}\r\na=rtpmap:0 PCMU/8000\r\n{te}"
    )


def send_rfc4733(rtp, dest, digit, ssrc=0x1234ABCD, seq=100):
    """one key press as RFC 4733 packets: start, 2 updates, 3x end (marker + E bit)"""
    ev = EVENTS.index(digit)
    ts = int(time.time()) & 0xFFFFFFFF
    for i, (end, dur) in enumerate([(0, 160), (0, 320), (0, 480), (1, 640), (1, 640), (1, 640)]):
        hdr = struct.pack(">BBHII", 0x80, DTMF_PT | (0x80 if i == 0 else 0), seq + i, ts, ssrc)
        rtp.sendto(hdr + bytes([ev, (0x80 if end else 0) | 10]) + struct.pack(">H", dur), dest)
        time.sleep(0.02)


def send_info(phone, sim_addr, ok_invite_to, inv, digit, cseq):
    """in-dialog SIP INFO dtmf-relay from the phone (callee) side"""
    p = Packet()
    p.status_line = f"INFO sip:10000000009@{LOCAL}:{sim_addr[1]} SIP/2.0"
    p.set("Via", f"SIP/2.0/UDP {LOCAL}:{phone.getsockname()[1]};branch=z9hG4bKinfo{cseq}")
    p.set("From", ok_invite_to)  # our (callee) side: To of the INVITE + tag
    p.set("To", inv.get("From"))
    p.set("Call-ID", inv.get("Call-ID"))
    p.set("CSeq", f"{cseq} INFO")
    p.set("Content-Type", "application/dtmf-relay")
    p.body = f"Signal={digit}\r\nDuration=160"
    p.set("Content-Length", str(len(p.body)))
    phone.sendto(str(p).encode(), sim_addr)
    return recv(phone, "SIP/2.0 200", 2)[0] is not None


def start_sim(tmp, ports):
    env = Path(tmp) / "sim.env"
    env.write_text(
        f"INDOOR_SERVER={LOCAL}\nINDOOR_SERVER_PORT={ports['reg']}\nINDOOR_USERNAME=10000000009\n"
        f"INDOOR_PASSWORD=x\nINDOOR_NAME=SimCall\nINDOOR_LOCAL_IP={LOCAL}\n"
        f"INDOOR_LISTEN_PORT={ports['sim']}\nINDOOR_RTSP_PORT={ports['rtsp']}\n"
        f"INDOOR_SIP_HOST={LOCAL}\nINDOOR_SIP_PORT={ports['phone']}\nINDOOR_SIP_TARGET=6000\n"
        "INDOOR_HEARTBEAT_INTERVAL=600\nINDOOR_DEBUG=1\n"
        f"INDOOR_DOOR_ISAPI_PORT={ports['isapi']}\nINDOOR_DOOR_ISAPI_PASS={ISAPI_PASS}\n"
    )
    log = Path(tmp) / "sim.log"
    proc = subprocess.Popen(
        [sys.executable, "-m", "metzler"],
        env={**os.environ, "INDOOR_ENV": str(env), "PYTHONPATH": str(ROOT / "src")},
        stdout=log.open("w"),
        stderr=subprocess.STDOUT,
        cwd=ROOT,
    )
    time.sleep(1.5)
    return proc, log


def free_tcp_port():
    s = socket.socket()
    s.bind((LOCAL, 0))
    port = s.getsockname()[1]
    s.close()
    return port


def expected_doors(digits):
    """doors that must get unlocked for the keys pressed ('#<n>' = door n)"""
    m = re.search(r"#(\d)$", digits)
    return [int(m.group(1))] if m and m.group(1) in "12" else []


def press(args, phone, rtp, sim_addr, sim_rtp, answer_to, inv):
    """play the phone's key presses over the selected transports"""
    cseq = 10
    for d in args.digits:
        if args.dtmf in ("rfc4733", "both") and d in EVENTS:
            send_rfc4733(rtp, sim_rtp, d)
        if args.dtmf in ("info", "both"):
            cseq += 1
            send_info(phone, sim_addr, answer_to, inv, d, cseq)
        time.sleep(0.4)


def run(args):
    door, phone, rtp = udp_socket(), udp_socket(), udp_socket()
    outdoor = FakeOutdoorStation()
    ports = {
        "isapi": outdoor.port,
        "sim": udp_socket().getsockname()[1],
        "reg": udp_socket().getsockname()[1],
        "phone": phone.getsockname()[1],
        "rtsp": free_tcp_port(),
    }
    with tempfile.TemporaryDirectory() as tmp:
        proc, log = start_sim(tmp, ports)
        sim_addr = (LOCAL, ports["sim"])
        try:
            door.sendto(str(door_invite(door.getsockname()[1], ports["sim"], args.kind)).encode(), sim_addr)
            inv, pa = recv(phone, "INVITE")
            assert inv, "the sim never INVITEd the phone"
            sim_rtp = (LOCAL, int(re.search(r"m=audio (\d+)", inv.body).group(1)))
            body = phone_answer_sdp(rtp.getsockname()[1], args.dtmf != "none")
            phone.sendto(response(inv, "200 OK", body), pa)
            recv(phone, "ACK")
            ok, _ = recv(door, "SIP/2.0 200")
            assert ok, "the door never got its 200 OK"
            answer_to = inv.get("To") + ";tag=phone1"
            rtp.sendto(b"\x80\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x01" + b"\xff" * 160, sim_rtp)
            time.sleep(0.3)
            press(args, phone, rtp, sim_addr, sim_rtp, answer_to, inv)
            time.sleep(0.8)  # the unlock PUT runs in the sim's worker thread
            doors = [d for d, _body, _ok in outdoor.unlocks]
            want = expected_doors(args.digits) if args.dtmf != "none" else []
            ok_ = doors == want and all(good and "<cmd>open</cmd>" in body for _d, body, good in outdoor.unlocks)
            print(
                f"phone pressed {args.digits!r} via {args.dtmf}; outdoor station unlocked doors {doors}; expected {want}"
            )
            print("PASS" if ok_ else "FAIL")
            return 0 if ok_ else 1
        finally:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
            if args.log:
                print(log.read_text(errors="replace"))
            door.close(), phone.close(), rtp.close()
            outdoor.shutdown()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dtmf", choices=("rfc4733", "info", "both", "none"), default="both")
    ap.add_argument("--digits", default="#1", help="keys the phone presses (default '#1')")
    ap.add_argument("--kind", choices=("door", "extension"), default="door")
    ap.add_argument("--log", action="store_true", help="print the simulator's debug log")
    sys.exit(run(ap.parse_args()))


if __name__ == "__main__":
    main()

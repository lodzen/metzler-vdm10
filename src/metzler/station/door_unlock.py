"""Door unlock over the outdoor station's ISAPI (HTTP digest, stdlib only).

The outdoor station (VDM10-VM-POE, fw V2.2.56) exposes the standard Hikvision
route `PUT /ISAPI/AccessControl/RemoteControl/door/<n>` with
<RemoteControlDoor><cmd>open</cmd></RemoteControlDoor> (n = 1..2).
"""

import logging
import re
import urllib.error
import urllib.request

log = logging.getLogger("indoor")

BODY = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<RemoteControlDoor version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">'
    "<cmd>open</cmd></RemoteControlDoor>"
)


def isapi_open_door(host, user, password, door, port=80, timeout=5.0):
    """momentary unlock of `door` -> True when the station answered statusCode 1"""
    url = f"http://{host}:{port}/ISAPI/AccessControl/RemoteControl/door/{door}"
    mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    mgr.add_password(None, url, user, password)
    opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(mgr))
    req = urllib.request.Request(url, BODY.encode(), {"Content-Type": "application/xml"}, method="PUT")
    try:
        with opener.open(req, timeout=timeout) as r:
            text = r.read().decode(errors="replace")
    except (urllib.error.URLError, OSError) as e:
        log.warning("ISAPI unlock door %d on %s failed: %s", door, host, e)
        return False
    ok = re.search(r"<statusCode>1</statusCode>", text) is not None
    log.info("ISAPI unlock door %d on %s: %s", door, host, "ok" if ok else f"rejected {text[:160]!r}")
    return ok

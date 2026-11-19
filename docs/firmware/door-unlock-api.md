# Door unlock via ISAPI (HTTP XML) — firmware internals

How `hicore` (Metzler VTH 2.0.24, rebranded Hikvision indoor station)
exposes **remote door-unlock** over its ISAPI REST interface, what XML it
actually parses, and where the request goes inside the firmware. Call
flow on the wire level: [`door-call-flow.md`](door-call-flow.md);
registration: [`intercom-registration.md`](intercom-registration.md).

Sources: `vdm10_1.0/isapi/{videointercom.c,videointercom_do.c,README.md}`,
`vdm10_1.0/misc/{net_sipClient_visNetServer.c.c,
net_sdkService_sdkUpload.c.c, net_sdkService_netFuncs.c.c,
event_ioEvent_talkBackIoEvent.c.c, net_sipClient_sip_client.c.c,
rest_comm_comm_rest_misc.c.c}`, `vdm10_1.0/gui/comSubDefine.c`,
`vdm10_1.0/visnet/visnet_server.c`, `vdm10_1.0/names/*.tsv`, plus the
URI table recovered from `hicore` `.data` (file `0x749cbc`…, PT_LOAD
maps file `+0x10000` = vaddr). Each claim is marked **verified**
(decompile- or table-cited) or **inferred**.

**TL;DR** — the endpoint exists on every unit: `PUT
/ISAPI/VideoIntercom/remoteOpenDoor` with a `<RemoteOpenDoor>` XML body.
But the dispatcher is gated to door-type devices (`getDevType() ∈ {1
outdoor, 4 wall, 5 villa}`); a room-type indoor station (`getDevType()
== 3`) accepts the route yet always replies *device-not-supported*. The
generic Hikvision `PUT /ISAPI/AccessControl/RemoteControl/door/<n>` /
`<RemoteControlDoor>` convention is **not** registered in this firmware.

> **Live-verified on the outdoor station (2026-11-02)** — the outdoor
> unit at `10.0.0.48` is itself a `VDM10-VM-POE`, **firmware V2.2.56**
> (build 230608), a newer build than the indoor 2.0.24 analysed below, and
> it *does* serve the generic route. HTTP Digest, user `admin`:
>
> | request | result |
> |---|---|
> | `GET /ISAPI/System/deviceInfo` | 200, model/serial/firmware |
> | `GET /ISAPI/AccessControl/RemoteControl/door/capabilities` | 200: `doorNo min=1 max=2`, `cmd opt="open,alwaysOpen,resume"` |
> | `GET /ISAPI/AccessControl/RemoteControl/door/1` | 400 `methodNotAllowed` (route exists, PUT only) |
> | `GET /ISAPI/VideoIntercom/remoteOpenDoor/capabilities` | 404 `invalidOperation` (not in this build) |
> | `PUT /ISAPI/AccessControl/RemoteControl/door/1` body `<RemoteControlDoor version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema"><cmd>open</cmd></RemoteControlDoor>` | **200 `statusCode 1 ok`, the door unlocked (audible)** |
>
> The bridge uses this for `#<n>` (n = 1 or 2) on the phone
> (`station/door_unlock.py`). It is the only unlock mechanism the bridge
> uses: it sends no SIP INFO unlock.

## 1. Registered URI

Rows in the ISAPI URI table are `0x68`-byte records `{name*, handler*,
childTbl*, flags@+0x14}`; `/X`-named rows are group terminators — a
parent's children are the contiguous rows before its `/X` row and its
leaf row's `childTbl` points at the first child. Verified rows:

| vaddr | file | name | handler | notes |
|---|---|---|---|---|
| `0x756d34` | `0x746d34` | `/ISAPI` | — | group marker; children = `System, VideoIntercom, Event, Security, Image, AccessControl, Streaming, SecurityCP, ContentMgmt, SmartLock, SingleSmartLock` |
| `0x756924` | `0x746924` | `VideoIntercom` | — | `childTbl→0x75b7f4` |
| `0x75bb34` | `0x74bb34` | `remoteOpenDoor` | `0x001ced50` | inside the `/VideoIntercom` block (marker `0x75c14c`) |
| `0x75b174` | `0x74b174` | `capabilities` | `0x001cef7c` | sole child of `remoteOpenDoor` (marker `0x75b1dc`) |

`0x001ced50`/`0x001cef7c` are the real function entries (`cmp r1,#0;
cmpne r0,#0` prologue); Ghidra labels them `FUN_001ced58` /
`FUN_001cef84` a few bytes in:

- `PUT  /ISAPI/VideoIntercom/remoteOpenDoor` →
  `isapiIntercomRemoteOpenDoorBasic` @ `0x001ced58`
  (`vdm10_1.0/isapi/videointercom.c:518`)
- `GET  /ISAPI/VideoIntercom/remoteOpenDoor/capabilities` →
  `isapiIntercomRemoteOpenDoorCapBasic` @ `0x001cef84` (line 552)

A second `/ISAPI` marker exists at `0x75e41c` — the tree appears
registered twice (inferred: HTTP/HTTPS or a legacy copy).

## 2. Request

```http
PUT /ISAPI/VideoIntercom/remoteOpenDoor HTTP/1.1
Content-Type: application/xml
```

```xml
<?xml version="1.0" encoding="UTF-8"?>
<RemoteOpenDoor>
  <gateWayIndex>1</gateWayIndex>
  <command>unlock</command>
  <controlSrc>remote-http</controlSrc>
</RemoteOpenDoor>
```

Exactly three child tags are parsed by `isapiParseTagRemoteOpenDoor`
@ `0x001d2324` (`vdm10_1.0/isapi/videointercom_do.c:171`), into a
`0xac`-byte `ControlGateway` buffer:

| tag | effect | constraints (verified) |
|---|---|---|
| root `RemoteOpenDoor` | — | wrong root tag → `0x60000000` |
| `gateWayIndex` | `out+0x04` u32 | must be a non-negative integer, else `0x60000000`; executor requires **exactly 1** |
| `command` | `out+0x08` u8 | only literal `unlock` sets `1`; anything else is logged and **ignored** (stays 0 → executor rejects) |
| `controlSrc` | `out+0x0c` char[0x20] | value ≤ `0x1f` bytes, else `0x40000006`; executor requires non-empty |
| *(unparsed)* `out+0x09` | `byLockType` | **no tag reads it** — always 0 via HTTP |
| *(unparsed)* `out+0x30` | `password` | **no tag reads it** — always empty via HTTP |

Nothing else is parsed: no `cmd`/`open`/`close`/`alwaysOpen`/`resume`,
no `version`/`xmlns` requirement on the root, no lock id, no
`controlType`. Unknown tags are silently skipped.

The capability document (GET …`/capabilities`, built by
`isapiAddRemoteOpenDoorCapToXml` @ `0x001d2868`) advertises the same
shape: `gateWayIndex min="1" max="1"`, `command opt="unlock"`,
`controlSrc min="1" max="31"`.

## 3. Execution — `isapiExecuteRemoteOpenDoor` @ `0x001d2538`

(`vdm10_1.0/isapi/videointercom_do.c:199`; raw decompile
`FUN_001d2538`, `vdm10_1.0/decompiled/isapi_videointercom_isapi_videointercom_do.c.c`)

```
switch (byLockType):
  case 0:                    /* normal door lock — the only HTTP-reachable case */
      gateWayIndex must == 1, byCommand == 1, controlSrc non-empty
      msg[0x21]: msg[0] = 5; snprintf(msg+1, 0x20, controlSrc)
      vns_opendoor_up(msg)   -> return 0x10000000
  case 1:                    /* smart-lock/gateway — dead via HTTP */
      index 1..3, cmd 1, controlSrc AND password(+0x30) non-empty
      -> return ok WITHOUT posting anything
  default -> 0x40000006
```

Lock type 1 returns success but performs no action, and the HTTP parser
never populates `byLockType`/`password`, so via ISAPI only the type-0
path ever runs — index **1** is the only valid door number through this
endpoint. A parallel binary-SDK implementation exists in
`netClientRemoteControlGateWay` @ `0x000ac474`
(`misc/net_sdkService_netFuncs.c.c:1776`): same `{1,4,5}` devType gate,
same `index==1 && cmd==1 && controlSrc && lockType==0` checks, same
`{5,controlSrc}` → `vns_opendoor_up` — but gated on
`netu_verify_user(ctx, peer_ip, 0x20000)` instead of ISAPI auth.

## 4. Down the queue — `vns_opendoor_up` @ `0x001085f0`

(`vdm10_1.0/misc/net_sipClient_visNetServer.c.c:6151`; verified)

```c
void vns_opendoor_up(msg) {                    /* msg = {type u8, src[0x20]} */
    memset(buf, 0, 0xc);
    notifySdkUploadOpenRecord(msg);            /* @0x000c9b84, sdkUpload.c */
    mqSend(*DAT_00108640, buf, 0xc, 200, 0);   /* *0xa6c95c = /ioOutEventMsgQ */
}
```

`notifySdkUploadOpenRecord` @ `0x000c9b84`
(`misc/net_sdkService_sdkUpload.c.c:656`) logs
`"upload open door info, type:%d, details:%s"` and builds a `0x8fc`
open-door record `{kind 0, time, type, details}`: when `sip_devMode6()
== 0` it is `mqSend`'d to the SDK upload queue (mqd @vaddr `0x753218`;
the consumer is the SDK alarm-upload worker `alarmUpTask` @ `0x000afa48`,
`netFuncs.c` — *inferred*, queue open not recovered); otherwise it goes
to `db_opendoor_insert` @ `0x000fbf84` (db table 7, open-door log).

`/ioOutEventMsgQ` is drained by `talkBackIoOutEventTask` /
`gui_oversee_door_handle_msg_unlock_proc` @ `0x00108650`
(`misc/event_ioEvent_talkBackIoEvent.c.c`), whose `switch` on the
12-byte message's type field does: 2–5 LED on/off, 6–7 LCD on/off,
8 LED-blink, **0xa/0xb/0xc → alarm-output bitmask →
`ddm_set_alarm_out_status`** (`interface_kernel_ddm_interface.c.c`
@ `0x00051918`). The message `vns_opendoor_up` posts is *all zero*
(type 0 → default → no action) — so the mq post is a decompiled no-op
nudge, and the final relay actuation is **not** shown by this chain
(§8).

The same terminal runs door-side for **VisNet cmd `0x2001`**
(`VIS_NETCMD_UNLOCK`, `visnet_server.c` + `visNetServer.c.c` case
`0x2001`): the handler parses the caller's devNum, sets the record type
to `4` or `5` depending on caller devType, calls `vns_opendoor_up`, and
replies code `1` = "Open door success!". `vns_clientUnlock`
@ `0x000eec04` on the requesting station sends the `0xb4`-byte `0x2001`
packet (lockId @ `+0xa8`) and waits for the `0x1c`-byte reply. ISAPI's
record type is hard-coded `5`; the VisNet handler picks `4`/`5`
(`(devType & 0xfd) == 1 → 4`).

## 5. Device-type gate (the catch for an indoor station)

`isapiIntercomRemoteOpenDoorBasic` checks `getDevType()`
(`FUN_000f7e90`; 1 outdoor, 2 manage, 3 room, 4 wall, 5 villa,
6 confirm — `isapi/README.md:169`) **before** method/privilege checks:

- `devType ∈ {1,4,5}` → proceed (door units that own a lock).
- anything else — including **room-type indoor units (3)** →
  `0x40000004` → ISAPI "device not support". The Metzler VDM10 room
  station therefore rejects this endpoint even though the route is
  registered (gate code verified; whether a given production build
  reports devType 3 is config/board-dependent — *inferred* VDM10 ==
  room).

On the indoor unit the working door-open paths are instead:

| path | code (verified) |
|---|---|
| in-call SIP INFO DTMF `application/dtmf-relay` — lock 1 `Signal=#\nDuration=160`, lock 2 `Signal=2` (then `#`) | `sipcli_unlock_dtmf` @ `0x000df6c0`, `net_sipClient_sip_client.c.c:3083` |
| VisNet cmd `0x2001` `vns_clientUnlock(ip,lockId)` (monitor-screen unlock) | `0x000eec04`; GUI `gui_UnlockTouchKeyMessageHandle` @ `0x0026ff64` (door 1) / `_1` @ `0x002701e4` (door 2) → `sipcli_unlock_dtmf(0\|1, lockNo, devType)` (`gui/comSubDefine.c:288`) |
| VisNet `0x2016` `vns_exUnlock(opType,lockId,doorType,ip)` — door-side opType 0 → local `sipcli_unlock_dtmf`, 1 → `vns_clientUnlock` | `0x000f0504`; server case `0x2016` @ `visNetServer.c.c:5173` |
| VisNet `0x2017` `AGAIN_UNLOCK` — compares cfg `+0x7c0`[0x20] unlock-pwd AND `+0x7e0`[0x10] hijack-pwd, then same `vns_opendoor_up` | server case @ `visNetServer.c.c:5198`; pwds set via cmd `0x2006` → `dpi_register_acs_pwd_info` @ `0x000fc084` |
| gateway-unlock helper (devNum → door IP → `vns_clientUnlock`), used by Hik-Connect/Ezviz apiId 2 `{srcId,channel,lockId}` | `sipcli_gateway_unlock` @ `0x000dfc88`; `ezviz/third_party.c` |

## 6. Errors (all verified in dispatcher/parser/executor)

Return codes map to `<ResponseStatus>` in `statusCode` @ `0x001c47c8`
(`rest_comm_comm_rest_misc.c.c:156`): `statusCode` element = `ret >>
0x1c` (1 ok, 4 "Invalid Operation", 5 "Invalid XML Format", 6 "Invalid
XML Content"), `subStatusCode` from a per-class string table — verified
entries below.

| condition | ret | statusCode / subStatusCode |
|---|---|---|
| `devType ∉ {1,4,5}` | `0x40000004` | `4` Invalid Operation / `notSupport` |
| method ≠ PUT (≠ GET for `/capabilities`) | `0x40000003` | `4` / `methodNotAllowed` |
| privilege check fails | `check_privilege` ret | e.g. `0x40000001`/`0x40000002` `badAuthorization`/`lowPrivilege` |
| null arg / empty body | `0x30000004` | `3` Device Error / `NULLpoint` |
| XML alloc/parse failure | `0x50000001` | `5` Invalid XML Format / `badXmlFormat` |
| root tag ≠ `RemoteOpenDoor`, non-integer `gateWayIndex` | `0x60000000` | `6` Invalid XML Content / `badXmlContent` |
| `controlSrc` > 31 bytes | `0x40000006` | `4` / `invalidContent` |
| `gateWayIndex` ≠ 1, `command` ≠ `unlock`, empty `controlSrc` | `0x40000006` | `4` / `invalidContent` |
| success | `0x10000000` | `1` / `ok` |

## 7. Authentication

The HTTP layer is the vendored appweb/REST front end
(`misc/rest_comm_comm_rest_misc.c.c`): `restVerifyUserPasswd` inspects
the appweb auth struct and, for `pAuth->type == 2`, runs the digest
verify path against the stored credential — **HTTP Digest** is the
evidenced scheme. Every PUT dispatcher additionally calls
`check_privilege(ctx, 0x8000)` (write bit; GET uses `0x800000`).
Basic auth is **not** evidenced in the recovered code — don't assume it.

```sh
curl --digest -u admin:'<PASSWORD>' \
  -H 'Content-Type: application/xml' \
  --data-binary @- \
  'http://<DOOR_UNIT>/ISAPI/VideoIntercom/remoteOpenDoor' <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<RemoteOpenDoor>
  <gateWayIndex>1</gateWayIndex>
  <command>unlock</command>
  <controlSrc>remote-http</controlSrc>
</RemoteOpenDoor>
XML
```

Target a door-type unit for a real unlock; on a room-type VDM10 expect
`statusCode 4 / subStatusCode notSupport` (§5/§6). `curl -v` shows a
`401` + `WWW-Authenticate: Digest` challenge → resend.

> **Verified live (2026-11-19):** `GET /ISAPI/Streaming/channels/101/picture`
> on the outdoor station rejects HTTP Basic with `401`; with the Digest
> challenge answered it returns `200` + the JPEG frame (`admin` creds) —
> confirms the digest-only path on a second route family.

## 8. Unknowns / not established

- **Final actuation.** The ISAPI→`vns_opendoor_up` chain ends in (a) an
  open-door record upload/DB insert and (b) a *zeroed* 12-byte nudge on
  `/ioOutEventMsgQ` whose consumer only acts on msg types 2–0xc (type 0
  = no-op). Either the decompiler dropped a payload copy or actuation
  runs elsewhere (door-side firmware difference, alarm linkage, or a
  path not recovered in this indoor build). The VisNet `0x2001` path is
  proven to physically open the door — the peer replies "Open door
  success!" — but the exact GPIO/relay write is not pinned down here;
  the only `ddm_set_alarm_out_status` callers recovered are the
  ioOutEvent cases 0xa–0xc and boardtest.
- **Lock type 1 / password.** `isapiExecuteRemoteOpenDoor` case 1 needs
  `+0x30` password which no XML tag fills; no caller in this build was
  found that populates it (the SDK-side `netClientRemoteControlGateWay`
  only exercises the lockType-0 shape). Its post-validation behavior is
  unrecovered — it returns success without observable action.
- **`/ISAPI/System/IO/outputs/...` as an unlock.** The IO subtree
  exposes port config and `openDoorBtn`/`magneticStatus` useTypes
  (`io.c`), but no trigger/action child row was found under
  `outputs/<id>` (only `capabilities`) — not a verified unlock path.
- **`RemoteControlDoor` convention.** Neither
  `AccessControl/RemoteControl` (the `AccessControl` node's children
  are `DeployInfo/Device/Card/Door` only; `Door` → `param` → DoorParam)
  nor the `<RemoteControlDoor>` root exists in this build's URI table —
  the common Hikvision door API is absent.
- **Second `/ISAPI` tree.** A duplicate marker suggests a second
  registration; whether it adds routes wasn't fully walked.
- **`controlSrc` semantics.** Copied verbatim into the open-door record
  (`snprintf`, ≤31 chars); any protocol meaning of the string itself is
  unestablished.
- **Whether a VDM10 variant reports devType 4/5.** If a wall/villa
  variant of this hardware runs the same build, the endpoint would
  accept the PUT there; unverified per-device.

## 9. Function index

| addr | name | file |
|---|---|---|
| `0x001ced58` (entry `0x1ced50`) | `isapiIntercomRemoteOpenDoorBasic` | `isapi/videointercom.c` |
| `0x001cef84` (entry `0x1cef7c`) | `isapiIntercomRemoteOpenDoorCapBasic` | `isapi/videointercom.c` |
| `0x001d2324` | `isapiParseTagRemoteOpenDoor` | `isapi/videointercom_do.c` |
| `0x001d2538` | `isapiExecuteRemoteOpenDoor` | `isapi/videointercom_do.c` |
| `0x001d2868` | `isapiAddRemoteOpenDoorCapToXml` | `isapi/videointercom_do.c` |
| `0x001085f0` | `vns_opendoor_up` | `misc/net_sipClient_visNetServer.c.c` |
| `0x000c9b84` | `notifySdkUploadOpenRecord` | `misc/net_sdkService_sdkUpload.c.c` |
| `0x000fbf84` | `db_opendoor_insert` (db table 7) | `misc/dataManagement_database_db_api.c.c` |
| `0x000afa48` | `alarmUpTask` (0x8fc-record mq consumer) | `misc/net_sdkService_netFuncs.c.c` |
| `0x00108650` | `gui_oversee_door_handle_msg_unlock_proc` (`talkBackIoOutEventTask`, `/ioOutEventMsgQ`) | `misc/event_ioEvent_talkBackIoEvent.c.c` |
| `0x00051918` | `ddm_set_alarm_out_status` | `misc/interface_kernel_ddm_interface.c.c` |
| `0x000ac474` | `netClientRemoteControlGateWay` (binary SDK twin) | `misc/net_sdkService_netFuncs.c.c` |
| `0x000eec04` | `vns_clientUnlock` (VisNet `0x2001`) | `misc/net_sipClient_visNetServer.c.c` |
| `0x000f0504` | `vns_exUnlock` (VisNet `0x2016`) | `misc/net_sipClient_visNetServer.c.c` |
| `0x000fc084` | `dpi_register_acs_pwd_info` (cmd `0x2006`) | `misc/dataManagement_database_db_api.c.c` |
| `0x000df6c0` | `sipcli_unlock_dtmf` | `misc/net_sipClient_sip_client.c.c` |
| `0x000dfc88` | `sipcli_gateway_unlock` | `misc/net_sipClient_sip_client.c.c` |
| `0x0026ff64` / `0x002701e4` | `gui_UnlockTouchKeyMessageHandle[_1]` (door 1/2) | `gui/comSubDefine.c` |
| `0x000f7e90` | `getDevType` | `misc/util_common_visUtil.c.c` (`FUN_`) |
| `0x000f9ce0` | `devTypeFromNum` | `misc/util_common_visUtil.c.c` |
| `0x001c47c8` | `statusCode` (`<ResponseStatus>` builder) | `misc/rest_comm_comm_rest_misc.c.c` |
| `0x001c5410` | `restVerifyUserPasswd` (digest verify) | `misc/rest_comm_comm_rest_misc.c.c` |
| URI rows | `/ISAPI` `0x756d34` · `VideoIntercom` `0x756924` · `remoteOpenDoor` `0x75bb34` · `capabilities` `0x75b174` | `hicore` file `0x746d34/0x746924/0x74bb34/0x74b174` |

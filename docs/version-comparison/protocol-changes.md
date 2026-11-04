# Protocol / message-format deltas — V2.0.24 vs V2.2.100

Wire-visible changes between the builds. V1 facts from
`docs/protocol.md`, `docs/firmware/intercom-registration.md`, `ANALYSIS.md`
(live-verified); V2 facts from `VDM10_2.0/human_readable/` +
`VDM10_2.0/analysis/resolved-function-literals.json` (static).

## 1. SIP method set — unchanged

Both stacks handle `REGISTER`, `INVITE`, `ACK`, `BYE`, `CANCEL`, `MESSAGE`,
`INFO`, `OPTIONS` on the client leg (V2 `cli_ua_task` @`0x00143340`
diagnostics enumerate the same set: "receive MESSAGE new" / `<locknumXML>` /
`INFO` / `Signal=` / `OPTIONS` / invite / ack / cancel / bye).
Registrar still REGISTER + forwarding (`sip_server_dispatch_method`
@`0x00228d68`, `sip_server_route_invite` @`0x0022b780`,
`sip_server_route_message` @`0x0022dc6c`, `sip_server_route_bye_or_cancel`
@`0x0022f20c`, `VDM10_2.0/human_readable/sip/server/{dispatch,proxy}.c`).

## 2. User-Agent

| | Old | New |
|---|---|---|
| Client UA | `eXosip/3.6.0` (wire-verified, `ANALYSIS.md` §4) | `HKVS/2.0.0` — `cli_ua_task` @`0x00143340` calls `eXosip_set_user_agent(DAT_00144360)`; literal resolved in `resolved-function-literals.json` |
| HTTP UAs (context) | — | `User-Agent: Hikvision` on EZVIZ HTTP, `ZTEHttpClient/3.0.1 neon/0.25.1`, `EZVIZ ipc` (`decompiled/ezviz_app_sub.cpp.c`) — not SIP |

## 3. SDP / media offer

V1 offer (`ANALYSIS.md` §5, verified): `m=audio … RTP/AVP 0 2 101` →
`PCMU/8000`, `G726-32/8000`, `telephone-event`; `m=video … 96` →
`H264/90000` + `fmtp:96 packetization-mode=1;profile-level-id=4D001E`.
Intercom calls use the same **header-only** template (private attrs, no `m=`).

V2 (static, function `0x00127fa4`/`0x00139fa4` region +
codec table `0x00125dec`):

```sdp
v=0\r\no=%s 0 0 IN IP4|IP6 %s\r\ns=Talk session\r\nc=IN IP4|IP6 %s\r\nt=0 0
a=doorFloor:%d a=responseType:%d a=doorType:%d a=isSpecialType:%d
a=isConfirm:%d a=version:%s a=lockNum:%d          (private attrs; +version,+lockNum)
m=audio %d RTP/AVP 0 8 101
a=rtpmap:0 PCMU/8000 | 8 PCMA/8000 | 101 telephone-event/8000 | fmtp:101 0-16
m=video %d RTP/AVP 96
a=rtpmap:96 H264/90000
a=fmtp:96 profile-level-id=42801F;packetization-mode=1
```

Additional codec table @`0x00125dec` (the selectable/parsed set):

| PT | Codec | fmtp |
|---|---|---|
| 0 | `PCMU/8000` | — |
| 8 | `PCMA/8000` | `bitrate=64000` |
| 98 | `G7221/16000` | `bitrate=32000` (DSP enc/dec @`0x003b3774`/`0x003b3860`, `human_readable/misc/audio_drv.c`) |
| 100 | `MP4A-LATM/16000` | `profile-level-id=24;object=23;bitrate=64000` |
| 103 | `G726/8000` (G726_32) | `bitrate=24000` |
| 104 | `MP4A-LATM/16000` | `profile-level-id=2;object=2;bitrate=64000` |
| 115 | `OPUS/16000` | `bitrate=6400` |

Delta: `PCMA` joined the offer, G7221/MP4A-LATM/OPUS/G726_32 are in the codec
set, `IN IP6` exists in the templates, two new private attributes
(`a=version:`, `a=lockNum:`). `s=Talk session` and the `doorFloor/responseType/
doorType/isSpecialType/isConfirm` attrs are unchanged. H264 stays pt 96 but
fmtp `profile-level-id` differs (`42801F` vs `4D001E`).

## 4. XML vocabulary

Unchanged roots/fields: `regXML`, `regDevSerial`, `regDevMacAddr`,
`regDevName`, `floorNum`, `sipNum`, `locknumXML`/`lockNum`, `ringXML`/
`noRingBack`, `videoIntercomMsg` `header/method/action/num`,
`responseStatus`, `unlockPwd`/`unlockPasswd`/`hijackPasswd`, `delFlag`,
`deviceSN`, `ipAddr`, `devLongNum`, `roomNum`, `floor`, `zone*`, `alarmTime`,
`subDoorInfo`, `againdevice`, `ManageCenter`, `sipCmd`.

New in V2 (verified literals, owners in parentheses):

- **Registration**: `<version>V2.0.0</version>` in the *request* regXML
  (`sip_xml_build_registration_identity` @`0x001216ac`); `<version>`,
  `<random>`, `<isSummerTime>` in the *response*
  (`sip_server_register_device` @`0x002291b8`; parsed by client
  `cli_reg_resp_proc` @`0x001318cc`); `regPassword` fields
  (`xml_parse_result_v2` @`0x001237d8`, `isapi_set_iot_param` @`0x00315ecc`,
  `isapi_set_device_communication_to_json` @`0x0031fcb8`); `commPassword`,
  `commPasswordWithDoor`, `commPasswordWithConfirm` (`0x001237d8`).
- **SIP2.0 message envelope** (`xml_getSipInfoXml_v2` @`0x00120138`,
  `xml_parse_head_v2` @`0x00124a08`, `sip/xml_proc.c`): `<version>`,
  `msgId`, `uri` (`/SIP/`), `encrypt` = `true`/`false` + `withSalt`,
  `rui`, `phoneNo`, `serverAddr`; encrypted `<body>` payloads keyed by the
  registration `random`/reg-key (PBKDF2/AES-128-CBC, see registration.md).
- **Method/action words** replace/augment numeric ids in the v2 envelope:
  methods `Query`/`Control`/`Notify`/`Response`; actions `ConfirmInfo`,
  `OutdoorInfo`, `CameraInfo`, `QRInfo`, `Token`, `UnlockByPassword`,
  `PushStreamType`, `CallLadder`, `LeaveMessage`, `PickupWhenLM`
  (literals @`0x00120138`).
- **Result/error**: `Result`/`Success`, `errorCode`, `errorMsg`
  (`xml_parse_result_v2` @`0x001237d8`); `ringId` (door-bell),
  `responseStatusStrg` on the ISAPI side.
- **Password mgmt**: `oldPassword`/`newPassword` (`0x0031c1a4`); `msgType`
  on internal/SDK queues (`cli_msg_task` @`0x001363b0`, `0x00101f6c`).
- `sipNum` is absent from the V2 *request* builder's emitted set (serial/
  MAC/name/floor only; `sipNum` still parsed/used elsewhere — inferred
  demotion, not removal).

## 5. Heartbeat / MESSAGE shape

Same carrier: `MESSAGE sip:sipCmd@<registrar>:5065`, `Content-Type:
text/xml`, `videoIntercomMsg` `method=1 action=1` every **~119 s** (`0x77`
threshold, identical constant on both sides), skipped while network down or
devLongNum isn't 11 chars; `sipCmd` remains a virtual AOR.

- V1: `task_sipClient_HeartBeat` @`0x000e28dc` → `sipcli_send_msg`
  @`0x000dc9b0` (`vdm10_1.0/sip/client/heartbeat.c`); body via
  `xml_getSipInfoXml` @`0x000ea910`; from-ip/to-ip + own devLongNum.
- V2: `cli_heartbeat_task` @`0x00133668` → `cli_send_sip_cmd` @`0x0012c8bc`
  (`sip/client/{heartbeat,device_helpers}.c`); same field set plus serial;
  on failure posts registration event 5 + UI status 9 — heartbeat loss now
  actively trips re-registration (inferred from `sip_client_post_registration_event(5)`).

## 6. Auth headers — SEP and challenge are NOT the SIP Digest

V2 firmware contains three distinct auth vocabularies that must not be
conflated:

| Scheme | Where | Evidence |
|---|---|---|
| `Digest realm="Hikvision" … algorithm=MD5` | SIP `:5065` REGISTER (+ proxy paths) | `sip_build_digest_challenge` @`0x0022af50`, verifier @`0x0011b468` |
| `WWW-Authenticate: SEP CIPHER_SUITE="0"` / `Authorization: SEP DATA="…"` | SDK/talkback login layer, **not SIP** | `tbs_build_auth_req` @`0x001747bc`, `tbs_parse_auth_resp` @`0x00174ea0` (`human_readable/misc/noname.c`, raw `decompiled/noname.c`) |
| ISAPI challenge/sessionLogin (`challenge`,`salt`,`sessionID`,`sessionIDVersion`, SHA-256) | HTTP ISAPI only | `isapi_secur_isapi_secur.c` @`0x003039c8`/`0x00303548`/`0x00302888` (`sec_hmac_sha256`), gen_challenge @`0x0030864c` |

V1 had only the ISAPI `/Security/challenge` RSA exchange
(`vdm10_1.0/isapi/secur.c`, `isapi_sdk_srv_gen_challenge` @`0x001cb658`) —
sessionLogin/SHA256 is new. A SIP peer never sees SEP or sessionLogin.

## 7. Content-Type / charset variants

| Body kind | V1 | V2 |
|---|---|---|
| SIP XML bodies | `text/xml` | `text/xml` (unchanged — `cli_send_regist` @`0x00130c50`, `cli_send_sip_cmd` @`0x0012c8bc`, reg response builder @`0x002291b8`) |
| ISAPI/REST responses | `text/xml` | `application/xml; charset="UTF-8"` / `application/json; charset="UTF-8"` negotiated (`rest_negotiate_response_format` @`0x002e0b00`) |
| ONVIF-ish HTTP | — | `Content-Type: text/xml; charset=utf-8` (`onvif_create_http_header` @`0x001549b4`, familyVoice) |
| EZVIZ HTTP | `text/xml` | `text/xml; charset="utf-8"` (`decompiled/ezviz_app_sub.cpp.c`) |

## 8. Transport/encoding notes

- `text/xml` bodies are still LF/CRLF-mixed templates on the client; the V2
  registrar parses them with TinyXML — entity-escaping now matters
  (see `registration.md` checklist).
- V2 adds an XML writer API (`sip_xml_writer_*`, `sip/client/xml_writer.c`)
  producing the same tag vocabulary — output shape is preserved while the
  generator changed.
- `Expires` handling: V1 client sent fixed 900 (kinds 1/3/8) / door-reg
  `cfg+0x350ce`*60; V2 registrar **echoes** the request Expires in the
  response and the client derives per-role values (`cli_send_regist`
  role→expires; `cli_reg_resp_proc` reads `expires` from the response) —
  inferred role-based expiry.
- IPv6 appears end-to-end in V2 SIP (`sip_config_get_ipv6_mode` @`0x00230b2c`
  picks `AF_INET` vs `AF_INET6` sockaddr handling in
  `sip_server_register_device`; `IP6` SDP templates; `inet_pton` in
  `xml_parse_head_v2`). V1 was IPv4-only.

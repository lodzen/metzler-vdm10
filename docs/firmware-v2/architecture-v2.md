# V2 architecture — how the SIP stack differs structurally

V2.2.100 replaces the v1 C `net/sipServer` registrar (own parser, stateless
replies, `strstr` body scraping) with a **C++ sipsvrLib** — an embedded
service library of `service_sip_t` objects threaded onto worker pools,
fronted by a `sipDevSDK_*` callback API. The eXosip client leg (:5060) is
kept and extended. Sources: `VDM10_2.0/human_readable/sip/server/` (raw:
`decompiled/.._.._sipsvr_service_sip.cpp.c`, `.._.._sipsvr_device.cpp.c`,
`.._.._sipsvr_application.cpp.c`, `net_sipServer_sipsvr.c.c`).

## 1. Server side — sipsvrLib module map

| Layer | Code (address anchors) | Role |
|---|---|---|
| UDP workers | `transport.c`: `sip_server_initialize_udp_workers` @0x00226da0, `sip_receiver_thread_entry` @0x00226b24, `sip_sender_thread_entry` @0x00227760, `sip_server_receive_and_queue_datagrams` @0x00227d40, `sip_server_enqueue_outgoing_packet` @0x00227bf4, `sip_server_drain_send_queue` @0x00227790, `sip_server_stop_and_release_workers` @0x00227444 | recv→queue→work→send pools; buffer pool `sipsvr_bufpool_*` + `sipsvr_msg_buffer_alloc/free` |
| Work dispatch | `dispatch.c`: `sip_server_process_packet_work_item` @0x00227fb4 (work_routine), `sip_server_parse_and_dispatch_datagram` @0x00228744, `sip_server_dispatch_method` @0x00228d68, `svc_sip_packet_buf_recycle` @0x0022f1dc, `sip_release_packet_work_item` @0x00230098 | osip parse → method strcmp → per-method route; dispatch actions: −1 drop, 0 reply, 1 broadcast to online devices, 2 forward |
| Method handlers | `registrar.c` `sip_server_register_device` @0x002291b8; `proxy.c` `sip_server_route_invite` @0x0022b780 / `route_ack` @0x0022cfb4 / `route_message` @0x0022dc6c / `route_bye_or_cancel` @0x0022f20c / `route_response` @0x0022d598 | REGISTER + proxy forwarding with Via/Record-Route/R-URI/From rewrites |
| Replies | `reply.c` `sip_server_build_response_from_request` @0x0022a4c4; `credentials.c` `sip_build_digest_challenge` @0x0022af50 | cloned-header responses, tag/Expires/Server, Digest challenges |
| Device model | `device.c` `sip_device_record_construct` @0x0021efbc / `apply_registration` @0x0023ace0 / `apply_unregistration` @0x0023aec8 / `mark_online` @0x0021f32c / `mark_idle` @0x00230d2c / `is_online` @0x00230ddc / `decrement_expiry` @0x0021f68c; `addressbook.c` `add` @0x0021dea8 `find` @0x002310a4 `resolve` @0x002311a0 `erase` @0x0021e080 `load_registrations` @0x0021d590 `expire_heartbeats` @0x0021c82c `get_or_refresh_manager` @0x0021e4f0; `address_book.c` list/map helpers | in-memory C++ device records (std::string fields) in a locked addressbook; expiry worker |
| Persistence | `persistence.c` `rp_track_device_change` @0x00234624, `sip_registration_persistence_flush_events` @0x00234a10 (`rp_work`), `rp_dev_fields_upsert` @0x0023481c, `rp_find_q`/`rp_show_q`, rwlock set, `sipsvr_digest_ha1_hash` @0x00237014 / `sipsvr_digest_response_hash` @0x002371f0 | dev_reg DB write-through + HA1/response helpers |
| App/service shell | `application.c` `sip_application_initialize` @0x0021b430 / `start_services` @0x0021b8f8 / `stop`/`schedule_restart`; `service_mgr.c` `sip_service_manager_create_services` @0x002325c0 (+std::map tree helpers); `server_control.c` `sip_server_control_queue_loop` @0x0011b070, `sip_dispatch_sdk_server_event` @0x0011bb7c, `sip_start_server_control_thread` @0x0011c0c4; `multi_thread_work.c`, `work_thread.c`, `threading.c` | singleton app + service_mgr + control mq |
| XML/crypto helpers | `registration_xml.c` TinyXML doc/element wrappers; `md5.c` `sip_md5_*` (0x0022b6xx region); `server/md5.c` | TinyXML (not strstr) for regXML; osip MD5 reuse |

## 2. sipDevSDK callback table & API

`sip_start_server_control_thread` @0x0011c0c4 installs a 6-word callback
block and a 0x24 config block into `sipsvr_service_start_wrapper`
@0x0021af18 → `sip_application_initialize`. The SDK-facing API surface
(self-identified `sipDevSDK_*` names) lives in `sdk_callbacks.c`:

`get_local_devtype` @0x00224870, `is_dev_maindoor` @0x002249e8,
`get_Manager_center` @0x00224c78, `get_dev_LongNum` @0x00224fc0,
`get_Room_LongNum` @0x002252c0, `get_register_list` @0x00225490,
`get_reply_time` @0x00225608, `get_reg_sip_password` @0x00225828,
`get_reg_sip_random` @0x00225950, `get_reg_sip_key` @0x00225a34,
`save_reg_sip_key` @0x00225b84, `send_register_to_dev` @0x00225d14,
`send_authentication_to_dev` @0x002260a8, `sipsvr_status_to_dev`
@0x002263cc, `cb_get_reg_password` @0x0011a058,
`cb_get_runtime_info` @0x0011a2ac (selector 0–7: own num / dev kind /
registrar ip / room addr / ext list / numbering / time-DST / cli flag),
`cb_recv_sipsvr_msg` @0x0011bb7c. The sipsvrLib never touches the cfg blob
or DB directly — it calls back through these.

## 3. Config layout (sipsvrLib `config_t`, `sipsvr_config_load_defaults` @0x0023bd98)

| Off | Field | Source/default |
|---|---|---|
| +0x18 | listen port | `net_util_aux_q(1)` else `0x13c9` (5065) |
| +0x1c | numbering scheme | **10** (app) |
| +0x20 | body-skip/XML gate | 0 (always parse) |
| +0x24 | auth mode (SIP2.0 digest) | `vis_aux_f_q()==0` — prior work ties the getter to `cfg+0x35e8d` (SIP1.0/SIP2.0 byte) |
| +0x28 | floorNum/type-1 flag | `sip_sdk_attr5_flag_get` |
| +0x2c | IPv6 mode | `cfg_dev_get()!=0` |
| +0x30 | device kind | `sip_sdk_query_local_device_type` (==1 → indoor/type-1) |
| +0x34 | realm | `"Hikvision"` |
| +0x40 / +0x44 / +0x54 | labels | `sipServer` / `ManageCenter` / `MainDevice` |
| +0x50 / +0x58 | timers | 600 / 900 |

Device cfg offsets consumed via callbacks: `cfg+0x2d354` devLongNum,
`cfg+0x35a14` regPassword, `cfg+0x120` display name, `cfg+0x5fd0`/`0x5fe8`/
`0x601c`/`0x6034` peer IP blocks, `cfg+0x34fac`/`0x34fcc`/`0x34fec`/
`0x35000`/`0x35040`/`0x350ce` door-account block, `cfg+0x6018` doorType.

## 4. Client side — what stayed

eXosip leg: `cli_ua_task` @0x00143340 (:5060, `HKVS/2.0.0` UA) +
`cli_regist_task`/`cli_heartbeat_task`/`cli_send_msg_v2` — MESSAGEs now go
to **:5065** with digest auth and a response-flag wait (vs v1 fire-and-forget
:5065/:5060 unauthenticated). SIP2.0 additions vs v1: `<random>` key
machinery (registration_keys.c), `a=version:` SDP tag + INVITE digest,
fast_regist mq, v2 XML (`*_v2` builders/parsers in `sip/xml_proc.c`).

## 5. Key-management & adjacent crypto

- `system/key_manage.c` — `init_security_encrypt_key` @0x00381af8,
  `get_secure_encrypt_key` @0x003802d8, `get_module_key` @0x00381bbc,
  `genarate_work_key` @0x0037eaf4, `key_base64_{en,de}code`
  @0x00383588/0x00383778, `app_data_{en,de}crypt` @0x0037e7cc/0x0037e8e8 —
  the dev_key_manage-style key store the reg-key helpers sit next to.
- ISAPI side: `isapi_security_credential_hash` + `sec_hmac_sha256`
  @0x00302888 + `handle_isapi_security_challenge_request` @0x002fff7c /
  `handle_isapi_security_session_login_request` @0x00304a58 —
  SHA256 challenge/session auth is **ISAPI**, not SIP.
- Presence (not analyzed): `smartlock/` (client_api/protocol/radio/
  packet_codec/dispatch_thread), `misc/event_zigbee_rex_zigbee_application.c`,
  ONVIF-adjacent code in `misc/`/`net/` — **unverified** depth.

## 6. Structural deltas vs v1 (summary)

| v1 (V2.0.24) | v2 (V2.2.100) |
|---|---|
| C `net/sipServer` parser, `strstr` body scrape, stateless `sendto` replies, hard-coded :5060 reply port | C++ `service_sip_t` worker pools, osip parse, Via `received`/`rport` fix, response cloned from request |
| To-URI AOR identity | **From-URI username** identity |
| No digest generation found | Full Digest gate (challenge + verify), config-gated (SIP1.0/SIP2.0 byte `cfg+0x35e8d`) |
| dev_reg 0xb0 rows via `dpi_*` SQL | in-memory C++ records + `rp_*` persistence events → same `dpi_*`/`db_query` SQL |
| `g_callBusy` flag | `sip_device_is_online` + `cli_vistalk_*` state |
| eXosip/3.6.0 UA | `HKVS/2.0.0` UA |
| `<regXML>` request/`<version>` absent | `<version>` only in **response** (+ `<random>` AES/PBKDF2 key, `<isSummerTime>`) |
| MESSAGE fire-and-forget | MESSAGE → :5065 + digest + channel-table response wait |
| header-only SDP | same, + `a=version:V2.0.0` gate; 0xb keeps real RTP/AVP SDP w/ wider codec table |

# Hik-Connect / EZVIZ cloud channel — firmware internals (V2.2.100)

How `hicore` V2.2.100 talks to the Hik-Connect / EZVIZ cloud — the V2
counterpart of [`../firmware/hikconnect.md`](../firmware/hikconnect.md).
Evidence: `VDM10_2.0/human_readable/ezviz/` (`ezviz_app_*.c`,
`alarm_*.c`, `p2p_*.c`, `cloud_*.c`, `kernel*.c`) plus
`isapi/isapi_secur_isapi_secur.c` and `gui/src_gui_civilset.c`.

## 1. Architecture

Same layering as v1: app code registers per-domain callbacks on a
statically-linked ezDevSDK subcore; one framed channel to DAS reached via
LBS, then multiplexed domains (base, preview, talk, third-party, alarm,
kernel-extend). Notable V2 additions: a dedicated **token machinery**
(bind/unlock/query tokens), a **`shadow_v3`** alarm domain family, and
p2pv3.

## 2. Token machinery (`ezviz/ezviz_app_sub.c`, `ezviz_app_third_party.c`, `ezviz_app_base.c`)

| Function | Address | Role |
|---|---|---|
| `ezviz_query_bind_token` | 0x0071fe70 | bind-token query/rsp — pairing token exchange for account binding |
| `ezviz_querytoken_rsp` | 0x00714390 | generic token response handling |
| `ezviz_pu2_open_get_token_rsp` | 0x006cb894 | `pu2open` transfer token response |
| `ezviz_wait_get_secretkey_rsp` | 0x0077b328 | waits for the `secretkey` response on the trans-protocol channel (self-identified via log string `wait_get_secretkey_rsp platform return error`); the response carries an embedded **RSA-1024** key used to encrypt session material upstream |
| `ezviz_sdk_device_token_set` / `ezviz_sdk_query_device_token` | 0x00697cc0 / 0x00698728 | store/read the device token handed to the SDK |
| `ezapp_thirdparty_get_token` / `ezapp_thirdparty_set_token_time` / `ezapp_thirdparty_set_token_lasttime` | 0x006919d4 / 0x00691a78 / 0x00691ae8 | third-party-domain token get + lifetime bookkeeping |

An **unlock token** path exists alongside the SIP-side
`cli_unlock_token_req_q` (`doorbell-pairing.md` §5): remote unlock from the
app rides the third-party domain into `ezviz_send_msg_to_dev`
@0x006d6fec → local SIP/lock functions — the exact unlock-token field flow
is **unverified** end-to-end.

## 3. Alarm & shadow_v3

`ezviz_register_alarm_shadow_v3_domain` @0x006afca0 and the batch
`ezviz_register_alarm_shadow_v3_domains` @0x006affa8 /
`ezviz_unregister_alarm_shadow_v3_domain` @0x006afc04
(`ezviz/alarm_runtime.c`) plus `ezviz_basefun_register_shadow_v3`
@0x0070bc74 and `shadow_v3_extend_data_route_cb` @0x0072e990
(`ezviz_app_sub.c`) — the v3 property/alarm "shadow" channel: JSON
`{BusiID,Type,Value}`-family payloads built/parsed by the large
`ezviz/alarm_json.c` builder/parser set (~50 typed alarm JSON builders:
defence status/plans, human/car detect, light linkage, custom voice,
loiter, mute, mic volume...). Alarm upload: `ezviz_send_alarm_req`
@0x006b9334, `alarm_send_msg_to_platform` @0x006c46fc,
`ezviz_make_json_report` @0x006c6448, `alarm_intelligent_app_req`
@0x006b1ef8, `alarm_target_lib_req` @0x006b2570, `alm_human_detect`/
`alm_human_filter`/`alm_switch_enable`/`alarm_light`
@0x006b49b4/0x006b6bf8/0x006b6cfc/0x006b7784, `ezviz_tiny_video_pic_push`
@0x006c83ac + `ezviz_make_tiny_video_pic_http_post_msg` @0x006c72e8
(alarm JPEG over HTTP POST).

## 4. Binding & QR

- QR payload generation lives in the civilset GUI (`gui/src_gui_civilset.c`,
  `gui/mailboxverifi.c` — `QRstr`/verification-code references); as in v1
  the QR string carries the cloud-binding URL + operator code —
  **unverified** in detail for V2.
- Bind token exchange: `ezviz_query_bind_token` @0x0071fe70 (above).
- OEM/Guards selection: the paramLib/Guards-vs-OEM host selection pattern
  exists as in v1 (ys7/ezvizlife domain table); the V2 function-level
  mapping is **unverified** — only the token/shadow layer was traced.
- ISAPI-over-cloud + session crypto: `isapi_msg` @0x006ce6a8,
  `ezviz_send_isapi_msg` @0x006cacd0 (HCNetSDK-style tunnel). Device-side
  ISAPI auth: `handle_isapi_security_challenge_request` @0x002fff7c →
  `isapi_sdk_srv_gen_challenge`, `handle_isapi_security_session_login_request`
  @0x00304a58, `isapi_security_credential_hash` (SHA256(user+salted
  challenge) → lowercase hex) and `sec_hmac_sha256` @0x00302888
  (`isapi/isapi_secur_isapi_secur.c`) — **the challenge/SEP flow is ISAPI,
  not SIP**; SIP2.0's `<random>`/key machinery is a separate SIP-side path.

## 5. P2P / talk / preview

- `ezviz_p2pv3` @0x006d7c84 + `p2p_talk_recv_thr` @0x006d7fb0 +
  `p2p_workers.c` (`p2p_join_worker_threads` @0x00747944) — P2P v3 NAT
  traversal/punch workers (`ezviz/p2p_format.c`, `p2p_errors.c`).
- Talk: `ezviz_ez_dev_sdk_talk_init` @0x006d1540,
  `ezviz_ez_dev_talk_set_client_type` @0x006d1a74,
  `ezviz_ez_dev_talk_authority_handle` @0x006d1f54,
  `ezviz_start_talk_req_imp_v3` @0x006d2520 /
  `ezviz_stop_talk_req_imp_v3` @0x006d282c, TTS header senders
  `ezviz_send_tts_header{,_v3}` @0x006d2c50/0x006d2adc,
  `talk_area_req` @0x006d6578, talkbuf ring @0x006d7714..
- Preview: `ezDevSDK_preview_init` @0x006d89b0 / `fini` @0x006d8d38,
  `ezviz_ez_dev_sdk_preview_send` @0x006d8e70,
  `preview_client_stream_ctrl_pre_process` @0x006d9594, `pv_msg2_send`/
  `pv_msg8_send`, `pv_response_result` — app-view live stream; gated by
  `cli_vistalk_busy_get`/`cli_vistalk_mode_get` (`mode==2`,
  `ezviz_app_stream.c`) so an in-progress local SIP call blocks/adapts the
  cloud preview.
- Call pushes: `ezviz_thirdparty` @0x006cc3c0 +
  `tpr_ext_msg_proc` @0x006cfc4c + `ezviz_thirdparty_extend_event`
  @0x006cfd98 — third-party-domain call signalling (apiId-family);
  `ezviz_call_forwarding`/`ezviz_app_call_forwarding.c` for the
  call-forward feature; `ezviz_app_leave_message.c` +
  `leave_message_recv_cmd` @0x006a1de0 for leave-message.

## 6. Unverified / open items

- Exact `secretkey` RSA-1024 usage (encrypts what field upstream).
- Whether the v1 0xE9ACBA9E ezdev frame + MD5 trailer is unchanged —
  transport internals largely live in the external SDK subcore here.
- Token lifetime policy; Guards/OEM host table contents in V2.
- Which alarm JSON types actually fire on this build's sensors.

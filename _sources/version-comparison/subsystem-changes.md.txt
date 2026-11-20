# Subsystem-by-subsystem diff — V2.0.24 vs V2.2.100

V1 tree: `vdm10_1.0/` (plus `docs/firmware/*.md`). V2 tree:
`VDM10_2.0/human_readable/` (reading view) + `VDM10_2.0/decompiled/` (raw
exports, cited when the module name carries evidence). Status words follow
the new tree's own review labels; `(inferred)` marks our interpretation.

## SIP server (local registrar, :5065)

| | V2.0.24 | V2.2.100 |
|---|---|---|
| Language/design | C, single-threaded stateless forwarder + own parser | **C++ `sipsvrLib`**, worker-thread SDK service |
| Source modules | `vdm10_1.0/sip/server/{sipServer,registrar,reply,forward,msg_parser,parse_uri/via/fline/contact/expires,sipServer_helpers}.c` | `VDM10_2.0/decompiled/.._.._sipsvr_{service_sip,device,addr_book_mgr,register_persistence,application,service_mgr,work_thread,multi_thread_work,sip_utils}.cpp.c` + `net_sipServer_sipsvr.c.c` + `.._sipsvrLib_venus_tinyxml_{tinyxml,tinyxmlparser}.cpp.c` — i.e. service_sip / sip_utils / addr_book_mgr / device / register_persistence / application / service_mgr / work_thread (+ multi_thread_work pool) exactly as a vendored C++ lib |
| Entry | `task_sipServer` @`0x000db51c`, `sip_receiveMsg` @`0x000db23c` → `sip_udpProcRule` @`0x000dabb8` | `sip_start_server_control_thread` @`0x0011c0c4` → `sipsvr_service_start_wrapper` @`0x0021af18` / `sipsvr_service_run` @`0x0021af74`; recv/send worker threads @`0x00226b24`/`0x00227790`; dispatch `sip_server_parse_and_dispatch_datagram` @`0x00228744` → `sip_server_process_packet_work_item` @`0x00227fb4` → `sip_server_dispatch_method` @`0x00228d68` |
| Parser | hand-rolled `parse_message`/`extract_aor`/`srv_parseMsg` | **osip** objects (`osip_message_*`, `osip_to/from/via/call_id/cseq_clone`, `osip_authorization_*`, `osip_build_random_number`) + **TinyXML** for bodies |
| Device table | flat `dev_reg` rows via `db_*`/`dpi_*` C API | `addr_book_mgr` C++ objects: `sip_addressbook_*` (lock-protected rb-tree), `sip_device_*` records with `std::string` fields (`device.c`) + `register_persistence` (`rp_*` @`0x0023454c`…) flushing to sqlite |
| Helpers | none comparable | `sipsvr_work_pool_*`/`sipsvr_task_*`/`sipsvr_bufpool_*` (`multi_thread_work.c`), `hpr_*` semaphore/thread/socket shims (`dispatch.c`,`work_thread.c`), own MD5 (`sip/server/md5.c` @`0x00237568`+), logging w/ source context |
| App<->SIP API | direct calls | `sipDevSDK_*` callback API — `sipDevSDK_cb_get_reg_password` @`0x0011a058`, `cb_get_random` @`0x0011abe4`, `cb_get_reg_key_base64` @`0x00119d74`, `cb_save_reg_key_base64` @`0x0011a108`, `cb_recv_sipsvr_msg` @`0x0011bb7c`, `get_local_devtype`/`is_dev_maindoor`/`get_Manager_center`/`get_dev_LongNum`/`get_Room_LongNum`/`get_register_list`/`get_reply_Time`/`send_register_to_dev`/`send_authentication_to_dev`/`sipsvr_status_to_dev` (`sip/server/sdk_callbacks.c`, 32 `sipDevSDK` literals per INVENTORY scan; V1 count 0) |

## SIP client (:5060)

| | V2.0.24 | V2.2.100 |
|---|---|---|
| Stack | eXosip2/osip2 via PLT into `misc/libeXosip2.so` (thunks @`0x0000f9xx`, `vdm10_1.0/ghidra_out/func_list.txt`) | eXosip2/osip2 via PLT into the same-named `.so`s (thunks @`0x00019xxx`, `VDM10_2.0/human_readable/imports/linkage_thunks.c`); `.so` files still ship (`extracted-file-comparison.json`, content-changed) |
| Tasks | `task_sipClient_UAS`, `RegistCtrl`/`voip_regist_task`, `HeartBeat`, `SipSessionCtrl`, `NewCall01/02`, `AlarmProc`, `PasswdProc`, `ringBack` | same set plus **`fast_regist`**: `eXosip_client_fast_regist_message_task`, `fast_regist_msg_send_q` @`0x00126018`, `cli_fast_regist_task` @`0x00126654`, `fast_regist_proc_q` @`0x00126a14`, `cli_transfer_msg_task` @`0x00138d78` (new task names in `cli_ua_task` diagnostics) |
| Registration crypto | none | `sip/client/registration_keys.c`: load ext key @`0x0012b088`, rotate random @`0x0012b1d0`, AES-128-CBC enc @`0x0012b32c`/dec @`0x0012b520`, credential check @`0x0012b724` (`%s_%s` secret) |
| XML | `xml_getRegInfoXml`/`xml_getSipInfoXml`/`xml_pares`/`lockNum/ring` builders | same + **`_v2` layer**: `xml_getSipInfoXml_v2`, `xml_parse_head_v2`, `xml_parse_result_v2`, `xml_pares_sip_info`, structured `sip_xml_writer_*` (`sip/client/xml_*.c`) |

## visNet (TCP :8102)

Parity, not a rewrite: `vns_nonce_gen` (hex-MD5 of `"Vis:Hikvision:%s"`)
@`0x0011c2c4` vs `0x000f51e0`; `vns_nonce_key_fold` @`0x0011c3b4` vs
`0x000f52c8`; `vns_md5_sign` @`0x0011c3f4` vs `0x000f5308`; `vns_payloadXor`
equivalent; server bind `0x1fa6`; cmd `0x2033` table push still handled
(`VDM10_2.0/human_readable/visnet/server.c` — function comments explicitly
note "unique cross-version match to v1"). `vns_registrar_sync_hook` still
fires after registration events (`sip/server/server_control.c`).

## ISAPI / REST

- Route table regenerated: `VDM10_2.0/human_readable/isapi/routes/*.c` +
  `serialization/*.c` (~70 serializers) vs V1's 146-entry URI table
  (`docs/firmware/isapi-endpoints.md`, `vdm10_1.0/isapi/`).
- **New auth**: `sessionLogin` w/ `challenge`/`salt`/`sessionID`/
  `sessionIDVersion` + SHA-256 (`sec_hmac_sha256` @`0x00302888`,
  `isapi_get_sessionlogincap_configuration` @`0x00303548`, session table in
  `isapi_secur_isapi_secur.c`) alongside the retained RSA `/Security/challenge`
  (`genChallenge` @`0x000cf5ac`, `nl_challenge_login` @`0x000cf6c8`,
  `nf_gen_challenge` @`0x000e1ea4`, `RSA_public_encrypt` strings).
- **AES-128-CBC + salted credential plumbing**: `aes_128_cbc_encrypt_pkcs7padding`,
  `net_msg_aes128_encrypt`, `irreversiable_msg_encrypt`, `is_encrypt:%d, withSalt:%d`
  literals @`0x000d94cc`/`0x000d9694`/`0x000d9a58`/`0x0017f35c`.
- New serializers: `intercom_protocol_type` (`opt="Compatible"`, @`0x00328bd4`),
  `sip_server`, `smart_lock_*`, `session_login_cap`, `ntp_server`,
  `temporary_password*`, `wireless*`, `input_proxy_channel*`, `security_log`,
  EZVIZ QR (`isapiSystemNetworkEZVIZQRCodeJsonGet` @`0x002f76c4`/`0x002f79e4`),
  EZVIZ unbind (`isapi_EZVIZ_unbind_basic` @`0x002f69a8`).
- `sdkSetStandardSipCfgV50` @`0x000d8fec` (standard-SIP config API);
  `"not sup Standard SIP"` diagnostics @`0x002f185c`/`0x002f5aa0`.

## EZVIZ / Hik-Connect

- V1: ezDevSDK + `protocol/ezviz_*.cpp` app layer (`vdm10_1.0/ezviz/`,
  `ezdevsdk/`; `docs/firmware/hikconnect.md`).
- V2: `human_readable/ezviz/` (33 files) — `alarm_shadow_v3` domain
  registration (`ezviz_register_alarm_shadow_v3_domain[s]` @`0x006afca0`/
  `0x006affa8`, domains `Microphone`/`IntelligentAPP`/`SoundSetting`/
  `Loitering`/…), `token` request in the SIP2.0 action vocabulary,
  `EZVIZQRCode` JSON API, `ezviz_third_party_remotecontrolgateway`
  @`0x0015c754`, `sadpc_get_devlist_ezviz_gateway` @`0x0017e8c0`,
  `DDM_IOC_*_EZVIZ_INFO` ioctls, `sSupportEZVIZUnbind` @`0x0022b780`.

## GUI

V1 `vdm10_1.0/gui/` ≈ 9 files. V2 `human_readable/gui/` ≈ 90 files +
`windows/` + `notifications/`. New screens/logic: `registerpassword.c`
(`gui_registerpassword_update_pwd_grade` @`0x0056a95c`, src
`src_gui_registerpassword.c.c`), `mailboxverifi.c`, `networkset_ipv6.c`,
`simplewizard_password*.c` (wizard-mandated password), `win_ezvizgateway`
(@`0x00560c20`+), `sub1g_doorbell.c`, `tamper.c`, `bulletin.c`,
`albums.c`, `family_voice*.c`, `deviceset/confrelatedev` regPassword writes
(@`cfg+0x35a14`). SIP diagnostics surfaced to UI/shell: `"sip server DNS
address is error…"`/`"sip server not response"` @`0x00527780`.

## Network

- V1: `vdm10_1.0/net/{netmisc,civil,ipcamera,rtp_talkback,vis_stream}.c`;
  dual-NIC concept (`netDualNic`, `netAltRoute`) already present.
- V2: `human_readable/net/` + **DHCPv6**: `net_netutil_aip_dhcpv6_client.c`
  (`aip_dhcp_start_dhcp6c` @`0x000c3ca4`, `dhcp_run_dhcp6c_script`),
  `rootfs/dhcp6c.tar.lzma` (new file); **policy routing**: `del/add ip route:
  %s table %d priority %d` literals @`0x000c920c`; `gatewayv6` DB column;
  RTSP subtree (`net/rtsp/`), multicast, SADP, arp_ping, `rest_comm` REST
  channel w/ TLS (`syslog_send_data_by_tls_cert` @`0x00036404`).

## Database

- V1: proprietary `db_*`/`dpi_*` API (`vdm10_1.0/misc/dataManagement_database_*`),
  `dev_reg`/`acspwd` tables.
- V2: same API surface **plus statically-linked sqlite** (`libraries/sqlite/`,
  `sqlite3_get_table` strings) and DDL for `room_extension_net_info`,
  `room_extension_route_info` (`sip_num`,`sip_pwd`,…), `alter table %s add
  column gatewayv6`, `db_v15_2_v16`/`db_v16_2_v17` migrations
  (literals @`0x0003b70c`/`0x0003d948`/`0x00042000`/`0x00044e10`).

## New devices/protocols in V2

| Addition | Evidence |
|---|---|
| Zigbee COO + sub1G | `misc/event_zigbee_rex_{smarthome_app,application,call_back,module}.c`, `wireless/detector_protocol.c`, `si44xx_*`, `sub1gDeviceDriveOtherManageTask`, `rex_export_gateway.c` |
| Smart lock | `human_readable/smartlock/{client_api,controller_state,device_methods,dispatch_thread,packet_codec,protocol,radio}.c`, `isapi_smartlock_*`, `single_smart_lock`/`smart_lock_*` serializers |
| ONVIF(-ish HTTP) | `onvif_create_http_header` @`0x001549b4` (familyVoice), ONVIF-style headers |
| `mkdosfs`-class formatter | FAT12/16/32 format literals @`0x0009a208`/`0x0009a3c0` (`"FAT16"`, cluster math) |
| wpa_supplicant | `libraries/hostap-wpa-supplicant/`, `libraries/wpa/`, `wifiset.c`, `src_gui_wifiset.c` |
| RTC/porch/elevator/thermostat/siren/white-list modules | `vendor/` + `misc/` file names |

## Binary bulk

- V1 hicore 7.7 MB, ~15.4k indexed functions.
- V2 hicore 12.7 MB, 25,382 indexed records (23,963 `.text` functions,
  `VDM10_2.0/README.md` coverage) — growth is mostly **statically-linked
  libraries**: `openssl-1.1.1v` (source-path literals), `curl`, `sqlite`,
  `srt`, `freetype`, `libjpeg`, `libpng`, `libnl`, `stlport` (C++ stdlib),
  `minigui`, `hostap-wpa-supplicant`, `si44xx`, `cjson`, `ezviz_cmarkup`,
  `hisilicon` (`VDM10_2.0/human_readable/libraries/`).
- New shared libs in `trees/misc/` (only_new, extraction): `libbsp_data_encrypt`,
  `libssp`, `libhwsdk`, `libcam_hal`, `libddm`, `libqrencode`, `libnl-3`,
  `libnl-genl-3`, `libsafefunc`, `libboundscheck` (memory-safe helpers used
  throughout: `memset_s`/`memcpy_s` calls are new-idiom), `log4j`, `dspProc`,
  `cam_default.xml` (`/etc/cam_iq/` path literal).
- `libeXosip2.so`/`libosip2.so`/`libosipparser2.so` still ship (changed,
  not removed) — although osip/eXosip are **absent from the new GPL list**:
  V1 ships `trees/app/gpl/Open_Source_Software_Licenses.cfg`; V2 ships
  `_NEU.cfg` + `_STD.cfg` selected at runtime (`gui_gpl_refresh_page`
  @`0x005581fc`; `.cfg` contents not retained — extraction-note claim).

# Configuration surface & gates — V2.0.24 vs V2.2.100

Where the two builds read their SIP behavior from. V1 offsets per
`docs/firmware/intercom-registration.md` + `vdm10_1.0/`; V2 offsets per
`VDM10_2.0/` (decompiled cfg reads + the serial-shell config dump
`shell_task_command_server` @`0x0008f56c`).

## 1. Raw cfg offsets

| Offset | V1 | V2 |
|---|---|---|
| own number (`devLongNum`, 11 chars) | `cfg+0x34fcc` (regXML `sipNum`); `cfg+0x2d354` used by heartbeat `<num>` | `cfg+0x2d354` — From/To username **and** the own-number match in `sip_lookup_registration_password` @`0x00119bb8`; also written by `gui/devicepairing.c` |
| Registration password | not separated — client auth used the generic SIP password path | **`cfg+0x35a14`** (`regPassword`, ≤32 B) — read by the registrar's own-number lookup *and* by `eXosip_add_authentication_info` in `cli_send_regist` @`0x00130c50`; written by `gui/{confrelatedev,devicepairing}.c`, `isapi_set_iot_param` @`0x00315ecc`, `isapi_set_device_communication_to_json` @`0x0031fcb8` |
| **SIP1.0/2.0 byte** | n/a (single private-SIP dialect + `Private`/`StandardSIP` mode) | **`cfg+0x35e8d`** — tested `==1` by `vis_aux_f_q` @`0x00161fd0`; the registrar's auth byte is initialized as `getter()==0` by `sip_start_server_control_thread` @`0x0011c0c4` (`VDM10_2.0/analysis/registration.md` E4; offset from prior investigation, the DAT indirection makes the raw decompile ambiguous — verified at the getter, inferred at the offset) |
| Site prefix (type/floor/unit) | `cfg+0x2d330` (type), `+0x2d332` (floor), `+0x2d334` (unit), class flag `cfg+0x2d335` | same split fields consumed via `sip_sdk_query_device_number_fields` in the 410 gate |
| Registrar address block | `cfg+0x5fd0` (registrar ip), `cfg+0x5fe8`, `cfg+0x6034`, `cfg+0x601c` role blocks | same offsets reused (`cli_send_regist`, `cli_heartbeat_task` still read `*cfg+0x5fd0` etc.) |
| Door pre-reg | `cfg+0x34fac/0x34fcc/0x34fec` numbers, `cfg+0x35040` port, `cfg+0x350ce` expiry-minutes | same role-9 path reads `+0x34fac…0x34fec`, `+0x35000/0x35044` host, u16 port, minutes×60 |

## 2. Named config fields (V2 dump, `shell_task_command_server` @`0x0008f56c`)

The debug shell prints the SIP configuration as named fields — new-side
vocabulary verified by literals:

- `SIP1.0` / `SIP2.0` — protocol-generation status label
- `sipServerIpAddr` / `sipServerIpAddrV6`
- `mainDoorIpAddr` / `mainDoorIpAddrV6`
- `regToDoor` / `regToCenter` / `regToConfirm` — per-target registration
  enable flags (roles 9/0xa in `cli_send_regist`)
- `sip server port: %d`
- plus IPv6 gateway fields (`Gateway`, `IPV6 Gateway`, `set/del gateway`,
  `gatewayv6` DB column @`0x00044e10`).

`mainDoorIpAddr:`/`devlongnum=%s, regToDoor=%d` also appear in client code
(@`0x00147c9c`, `0x00157b70`, `cli_get_subdoor_ip` @`0x00130038` region).

## 3. GUI / wizard gates

| Gate | V1 | V2 |
|---|---|---|
| SIP mode select | `Private` vs `StandardSIP` (ISAPI `IntercomProtocolType`, persisted `g_cfg+0x350cd`, `vdm10_1.0/isapi/videointercom{,_do}.c`) | `IntercomProtocolType` options now include **`Compatible`** (`serialize_intercom_protocol_type_xml_response` @`0x00328bd4`, `opt="Compatible"`); separate `sdkSetStandardSipCfgV50` @`0x000d8fec`; `"not sup Standard SIP"` diagnostics @`0x002f185c`/`0x002f5aa0` — i.e. "Compatible with SIP1.0 indoor station" ↔ "Enable Standard SIP" toggles |
| Registration password | optional/cosmetic (no verifier in old registrar) | **mandatory in GUI**: `gui/registerpassword.c` (password-grade widget `pwd_grade_*`), `simplewizard_password*.c` wizard flow; `regPassword`/`commPasswordWithDoor`/`commPasswordWithConfirm` fields pushed in indoor-info XML (`xml_parse_result_v2` @`0x001237d8`) |
| Mailbox verification | — | `gui/mailboxverifi.c` |
| IPv6 | — | `gui/networkset_ipv6.c`, `src_gui_networkset_ipv6_c.c` |
| NTP | `isapi/time.c` basics | `isapi/serialization/ntp_server.c`, `time_synchronization.c` (system) |
| Device mgmt (`[manage]`) | `vdm10_1.0/gui/exdesktop.c` contacts/exroom list | `gui/manage.c` (`gui_manage_delete_gateway` @`0x00540670`), device types incl. **SIP Server** role and **EZVIZ Gateway** (`win_ezvizgateway`, `sadpc_get_devlist_ezviz_gateway` @`0x0017e8c0`, `ezviz_third_party_remotecontrolgateway`) |

## 4. Runtime gates that decide REGISTER fate (V2)

| Gate | Where | Effect |
|---|---|---|
| `sip_config_is_reg_body_skip_enabled` @`0x00230a8c` (config+0x20==1) | `sip_server_register_device` @`0x002291b8` | `==0` → body is TinyXML-parsed (normal app path: init byte 0); `==1` also suppresses the `rp_track_device_change`→`sip_notify_device_registration_change` notify. Renamed pass23 (was `sip_config_is_digest_auth_enabled` — a mislabel; the digest gate uses +0x24) |
| `sip_config_get_auth_mode` @`0x00230a30` | same | `==0` → compat mode: classification + site-prefix **410 gate**; `==1` + Expires>0 → 401→Digest flow. Renamed pass23 (was `sipsvr_device_state_read`; reads config+0x24 on the registrar config object, not a device record) |
| `sip_config_is_device_mode_one` @`0x0021ef80` (config+0x30) | same | registrar is device-type 1 → activates the peer-class site compare |
| `sip_config_get_local_devtype` @`0x00230ac8` (config+0x30, init from `sipDevSDK_get_local_devtype()`) | `sip_classify_and_split_device_number` @`0x0023b194` | local device type 10/0xb → site-prefix branch vs other; **not** a numbering-scheme/digit-count selector. Renamed pass23 (was `sip_config_get_numbering_scheme`) |
| `sip_config_get_ipv6_mode` @`0x00230b2c` | response path | AF_INET vs AF_INET6 source-address decode |
| `vis_aux_f_q` @`0x00161fd0` (`cfg+0x35e8d==1`) | `sip_start_server_control_thread` | feeds the server-control init byte (local_2b) — the SIP1.0/2.0 personality switch; **factory default unknown** |
| `vis_pred_a/b/c_q`, `vis_dev_chk_*`, `cfg_dev_get` | client tasks | device-class predicates choosing primary/secondary NIC (`srv_primaryIpText`/`srv_secondaryIpText`), dual-NIC registration targets |
| `sipsvr_cfg_flag_0x28_check` @`0x00230af0` | numbering + XML parser | alternate number split + optional `floorNum` read |

## 5. What a provisioning client must know

1. `regPassword` (`cfg+0x35a14`) is a first-class field in V2 — it is the
   own-number HA1 input *and* part of the response-key derivation secret
   (`%s_%s`). Provisioning it wrong breaks both auth and the encrypted layer.
2. Extension passwords are per-row in the device DB (`sip_pwd` column),
   **not** the station regPassword — see `registration.md` checklist item 4.
3. The SIP1.0/2.0 byte changes *which handler semantics* run (compat vs
   auth, 410 gate) — a SIP-1.0 client needs "Compatible"/SIP1.0 mode or a
   provisioned password.
4. IPv6 configuration exists end-to-end; on dual-NIC the secondary address
   is used for the same registration roles as V1.
5. Don't derive defaults from strings: `SIP1.0`/`SIP2.0` labels and both
   code paths exist in the binary regardless of the configured value.

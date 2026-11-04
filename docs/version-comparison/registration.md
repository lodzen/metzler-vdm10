# Registration — V2.0.24 vs V2.2.100

The single most important difference between the two builds: the local
registrar (`:5065`) was **rewritten in C++** and registration now has a
configuration-dependent **mandatory Digest-auth gate**, a strict XML parser,
and an encrypted key blob in the 200 OK. Everything below is static-analysis
output; the definitive new-side report is
`VDM10_2.0/analysis/registration.md` and this doc condenses it against the
old tree (`docs/firmware/intercom-registration.md`).

## 1. Side-by-side

| Aspect | V2.0.24 (old) | V2.2.100 (new) |
|---|---|---|
| Registrar implementation | C, own kamailio-style parser: `sip_registerMode` @`0x000d8dcc`, `parse_message` @`0x000d7b40`, `vdm10_1.0/sip/server/{registrar,msg_parser,parse_*}.c` | C++ `sipsvrLib`: `service_sip_t` class + osip + TinyXML + `std::string`; `sip_server_register_device` @`0x002291b8` in `VDM10_2.0/human_readable/sip/server/registrar.c` (raw `decompiled/.._.._sipsvr_service_sip.cpp.c`) |
| Registrar port | UDP **5065**, `sip_udpServerInit` @`0x000da6f0` | UDP **5065** — numeric `0x13c9` used when configured port is 0, `sip_start_server_control_thread` @`0x0011c0c4` (`sip/server/server_control.c`); ASCII `5065` is gone from the binary but the port is not (`VDM10_2.0/analysis/registration.md`, INVENTORY literal scan) |
| Registration identity | **To-URI user** → `call_num`, `extract_aor` @`0x000d77a4`; must be exactly 11 chars else status `0x1a` | **From-URI username** — `request+0x7c→+4→+4`, `registrar.c` @`0x002291b8`. A client that puts the same user in From and To sees no difference; one that doesn't will be keyed differently |
| Authentication | **Effectively absent/cosmetic**: `send_reply`'s status table can emit `401`, but no digest verification or `WWW-Authenticate` generation exists in the decompiled `net/sipServer` — challenge origin unverified (`docs/firmware/intercom-registration.md` §2/§13) | **Mandatory when auth mode on**: getter `sipsvr_device_state_read` @`0x00230a30` ==1 and Expires>0 → start `401`, scan `Authorization` headers for quoted configured realm, verify MD5 → `200`/`403` (`0x193`); no match → stays `401` (`registrar.c` @`0x002291b8`; verifier `sip_verify_register_digest_authorization` @`0x0011b468`, `sip/server/credentials.c`) |
| Digest formula | Client side only: `eXosip_add_authentication_info(user, authuser, passwd, "MD5", NULL)` | RFC no-qop MD5: `HA1=MD5(user:realm:pw)`, `HA2=MD5(method:uri)`, `resp=MD5(HA1:nonce:HA2)` — `sipsvr_digest_ha1_hash` @`0x00237014`, `sipsvr_digest_response_hash` @`0x002371f0` (`sip/server/persistence.c`); challenge ctor `sip_build_digest_challenge` @`0x0022af50`, realm `"Hikvision"`, nonce, opaque, `algorithm=MD5`. Verifier requires Authorization-username == From-username and all fields present; empty password = reject, not bypass. **No working md5-sess** — the branch compares literal `MD5` vs `md5-sess` which is always false. No issued-nonce check seen (bounded observation) |
| Credential selection | single shared secret used by the client (`eXosip_add_authentication_info`) | **Per-number**: `sip_lookup_registration_password` @`0x00119bb8` — first 11 bytes match own number `cfg+0x2d354` → regPassword `cfg+0x35a14` (≤32 B); any other user → SQL `where sip_num = '<user>'`, password at row `+0x98` (≤32 B). An extension registers against *its own* provisioned password, not the station's regPassword |
| regXML in request | Optional — dev_type/serial/MAC/name **scraped by `strstr`** out of the raw body; malformed XML still registered | **Required fields**: TinyXML parse (`sip_parse_registration_xml_identity` @`0x002300e8`, `sip/server/registration_xml.c`); missing body/root/`regDevSerial`/`regDevMacAddr` or null text → fail. `regDevName`/`floorNum` conditional (device-mode / cfg flag). Root name not compared to `regXML` but conventional root is appropriate |
| XML strictness | `strstr` byte scrape — anything containing the substrings "parses" | TinyXML DOM parse — malformed XML (`&`, `<` in text) is a hard failure |
| Response shape | Stateless `send_reply` @`0x000d8cd0` to src port **5060**; status→code table @`0x00a6c084` (400/401/500/503/410/200); Via `;received=`+`;rport=5060`, To `;tag=` = CRC16-CCITT over {callid,cseq,from-uri} seed `b57ecd32…` (`build_res_buf_from_sip_req` @`0x000d7fa8`). Bare 200, no body | osip-built response `sip_server_build_response_from_request` @`0x0022a4c4` (`sip/server/reply.c`): To/From/Via/Call-ID/CSeq clones, **random to-tag** (`osip_build_random_number`), `Expires` echoed for REGISTER, `Date` header when available (`sip_sdk_query_reply_timestamp` @`0x00225608`), config-header (field40). On 200+Expires>0 an XML **body** is attached |
| Response body | none | `<regXML><version>V2.0.0</version><random>…</random><isSummerTime>0</isSummerTime></regXML>` (template literal @`0x002291b8`). `random` = base64 of 16-byte random AES-128-CBC(IV=0) under PBKDF2(secret,5000 iters,no salt,16 B) where secret = `snprintf("%s_%s", username, password)` — `sip_generate_registration_key_base64` @`0x00119d74`, `sip_load_or_generate_registration_random` @`0x0011abe4` (cached in DB row +0x100 hex/+0x140 ts, else `/dev/urandom`); stored to DB row +0xc0 (`sip_store_registration_key_in_database` @`0x0011a108`). It's a **key for later encrypted signaling**, not a request challenge |
| Compat-mode 410 gate | Subordinate registrar required matching site prefix; type-3 ROOM refused on analog systems (`sip_registerMode` step 4); status table contains `410 Sip Gone` but the trigger wasn't fully mapped | Explicit: when auth getter==0 (compat) **and** registrar device type==1 **and** peer class !=1 → compare site fields (byte1 type / halfword2 floor / byte4 unit) of local vs peer number → mismatch → `0x19a` (410) (`registrar.c` @`0x002291b8`; numbering @`0x0023b194`). In auth mode the classification is logged, not gated |
| Unregister (Expires 0) | `Expires==0` → `db_reginfo_del where serial_no='<s>' or dev_ip='<src>'` + map/ACS cleanup → 200. **Bodyless works** — strstr scrape tolerates it | **Bodyless breaks**: XML body is parsed *before* Expires is read, so an empty-body unregister takes the early exit (internal status `0x194`, no response built). Caller `sip_server_parse_and_dispatch_datagram` @`0x00228744` then serializes the *original request* and worker `sip_server_process_packet_work_item` @`0x00227fb4` handles that — likely a timeout or a reflected REGISTER, **not** a proven wire 404. With a body present: `sip_device_apply_unregistration` @`0x0023aec8` + `sip_addressbook_erase_device` |
| Numbering | `T FFF UU RRR II` via `sscanf("%01d%03d%02d%03d%02d")`, `srv_splitDevNum` @`0x000f9418` / `devTypeFromNum` @`0x000f9ce0`; `unit!=0 && room==0` → **type 1** (door) | Same split and same type-1 rule (`device_descriptor_out[4]!=0 && u16@+6==0 → 1`), `sip_classify_and_split_device_number` @`0x0023b194` (`sip/server/numbering.c`). Selector values 10/11 pick a *branch*, not number length — both format strings are the identical `%01d%03d%02d%03d%02d` (E5 literal resolution) |
| Extension range | `1000000000N` (prefix `strncmp(...,"1000000000",10)`), idx `01–05`, `idx>5` rejected by `sip_callLongNum`; door-call fan-out max **5** dev_reg rows (`talkBackExtensionPro` @`0x000e4ad8`) | `100000000`+idx prefix (9-char compares); extension/session slot bound `0x10`/`0x11` seen in call paths (`call_out.c` @`0x0013e774` region: `0x10 < uVar4` reject, slot countdown from `0x11`) — **16 inferred** |
| dev_reg semantics | Flat 0xb0-byte row keyed by `call_num`; dedup: same serial *or* dev_ip → replace; same num + different serial+IP + same dev_type → **reject**; `dpi_register_dev_reg_info` @`0x000fc4a0`; `reg_time` text expiry; `db_acspwd_gc` sweep | In-memory C++ **address book** (`sip_addressbook_*`: `find_exact_number`, `add_device`, `resolve_destination`, `erase_device`; `std::map`/rb-tree, `sip/server/addressbook.c`, `service_mgr.c`) keyed by From username + sqlite persistence (`dpi_register_device_registration_information`, `rp_*` flush @`0x00234a10`, `sip/server/persistence.c`; tables `room_extension_net_info` w/ `sip_num`,`sip_pwd`, `gatewayv6`; row +0x98 pwd, +0xc0 key, +0x100 random, +0x140 ts). Insertion of an unknown number does **not** provision a credential |
| Post-register side effects | `vns_registrarSyncHook` @`0x000f1490` → TCP :8102 cmd `0x2033` push of dev_reg table to `1000000000`-devices; `sip_insertCenterRecord` seeds `ManageCenter` | `vns_registrar_sync_hook` still called from the server-control queue (`sip/server/server_control.c` @~`0x0011b0xx`); `sip_insert_local_manager_registration` @`0x0011aebc` seeds the manager row; `sipsvr_forward_if_online` @`0x00230cd0` post-register; `sip_notify_device_registration_change` @`0x00225d14` |

## 2. Request/response packet comparison

V1 REGISTER (builder `_client_handle_register`/`sipcli_register` @`0x000e05e0`,
`vdm10_1.0/sip/client/registration.c`): `REGISTER sip:<registrar>:5065`,
`From: "name"<sip:<num>@<localip>>`, `Expires: 900` (kinds 1/3/8),
`Content-Type: text/xml`, `User-Agent: eXosip/3.6.0`, body =
`xml_getRegInfoXml` @`0x000eb2e4` — serial+MAC always, `<sipNum>`+`<floorNum>`
on kind 1, `<regDevName>` on kind 8, **no `<version>` emitted by this build**.

V2 REGISTER (builder `cli_send_regist` @`0x00130c50`,
`VDM10_2.0/human_readable/sip/client/registration.c`): roles 1/2/8/9/0xa;
`eXosip_register_build_initial_register` + `eXosip_add_authentication_info
(user, authuser, cfg+0x35a14, "MD5", 0)`; registrar default port `0x13c9`;
body = `sip_xml_build_registration_identity` @`0x001216ac`
(`sip/client/xml_messages.c`) — `<regXML>` root, **`<version>V2.0.0</version>`**
now emitted, `regDevName` (role 8), `regDevSerial`, `regDevMacAddr`,
`floorNum` (roles 1/0xa). UA `HKVS/2.0.0` (`cli_ua_task` @`0x00143340` →
`eXosip_set_user_agent`).

V2 response processing (`cli_reg_resp_proc` @`0x001318cc`): parses
`<version>`, `<random>`, `Date` header; the client then decrypts the key with
the same `%s_%s` secret / AES-128-CBC / PBKDF2 construction
(`sip_client_decrypt_credential_registration_key` @`0x0012b724`,
`sip/client/registration_keys.c`) — the blob is consumed by the peer, it is
not decorative.

## 3. What breaks / must change for a SIP-1.0-era client

Cross-referenced to `src/metzler/station/registration.py` and
`src/metzler/sip/digest.py` (current simulator).

| # | Check | Sim status | Required action |
|---|---|---|---|
| 1 | **From-URI user = identity** | OK — sim puts the same `username` in From and To (`registration.py:52-53`) | none; keep both headers equal |
| 2 | **Digest mandatory when auth mode on** | OK — 401/407 → MD5 retry (`registration.py:89-94`); `digest.py:20-44` implements the exact no-qop formula and preserves `opaque`; synthetic challenge verified (`VDM10_2.0/analysis/simulator-digest-check.json`) | none for the formula; ensure the device is in the mode you expect (see `config-and-gates.md`) |
| 3 | **Realm must match** | OK — realm copied verbatim from `WWW-Authenticate` | none |
| 4 | **Per-number password** | gap — sim sends one `--password` for its number | provision *that extension's* SIP password via the device's management workflow (writer not traced — do not invent an endpoint) and use it; do **not** register the main station's own number as a workaround |
| 5 | **XML must be well-formed** | gap — `reg_xml()` interpolates serial/name/MAC **without escaping** (`registration.py:36-43`); `&`/`<` breaks TinyXML | XML-escape text values |
| 6 | **Unregister needs the body** | gap — `Expires: 0` sends empty body (`registration.py:62`), V2 parses body *first* → early exit, binding likely stays stale, wire result is timeout/reflected request rather than a clean status | send the same `<regXML>` body on unregister |
| 7 | **Do not assume an error status on the wire** | n/a | an XML failure sets internal `0x194` but *no response object is built*; a timeout or echoed request is the plausible wire result — verify with a capture before diagnosing |
| 8 | **Response body ignorable for REGISTER itself** | OK — sim ignores 200 body (`registration.py:95-103`); acceptance is already decided | none for registration; **later** SIP2.0 MESSAGE/encrypted signaling needs the `random`+key (PBKDF2(`%s_%s`,5000)/AES-128-CBC/base64) — registration success is not proof calls work |
| 9 | **Number shape vs 410 gate** | depends on config | in compat mode a non-type-1 peer registering at a type-1 indoor is rejected `410` when site fields differ; a `TFFFUU000II` number (UU≠0, RRR=0) classifies type 1 and bypasses the gate |
| 10 | **Port** | OK — registrar still UDP 5065 default | none |
| 11 | **Expected flows** | — | `401→200` healthy; `401→403` = wrong password *for that number*; repeated `401` = realm/Authorization mismatch; `410` = compat-gate site mismatch; timeout = XML/config/transport — see diagnostic table in `VDM10_2.0/analysis/registration.md` |

## 4. Explicitly open (from `VDM10_2.0/analysis/registration.md`)

- Factory default of the auth/SIP-version byte (`cfg+0x35e8d`) — **unknown**;
  do not claim SIP2.0-or-auth is default.
- Peer-password provisioning UI/API writer — not traced.
- Exact wire behavior of the early XML/From-null exits — needs a capture.
- Encrypted SIP2.0 MESSAGE and call interoperability — out of scope of the
  registration verdict.

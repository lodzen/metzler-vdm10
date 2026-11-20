# What did NOT change — V2.0.24 vs V2.2.100

Deliberately conservative list: the things a simulator/peer can rely on
without knowing which build it faces. Verified = read in both decompiles;
live-verified = also captured on V1.

## Container & extraction

- `.dav` wrapper with cramfs at file offset **`0x6c`** (magic `453dcd28`),
  byte-identical to the carved `rootfs.cramfs` on both images
  (`VDM10_2.0/analysis/INVENTORY.md`, archive audit).
- Same encryption tooling: encrypted payloads (`app`, `vistalk`, `misc`,
  `audio`, `rv`, `start.sh`) decrypt with the **same 24-byte 3DES-EDE ECB
  key** (`21 02 03 4d 56 13 … 1d`, `ANALYSIS.md` §2 recipe applies to both).
- Same boot shape: tarballs → `/home/app` tmpfs → `/home/app/hicore`;
  stripped ARM32 LE EABI5 uClibc ELF, `/lib/ld-uClibc.so.0`.
- Same base system style: busybox-ish userland, `dec` helper, `*.tar.lzma`
  payloads, `hicore` as the one giant monolith.

## SIP wire contract

- **Registrar still UDP 5065** (`0x13c9` default, `sip_start_server_control_thread`
  @`0x0011c0c4` vs `sip_udpServerInit` @`0x000da6f0`); client leg still on
  :5060 (`eXosip` listen).
- **eXosip client leg** on both: `eXosip_register_build_initial_register`,
  `eXosip_add_authentication_info(…,"MD5",0)`, automatic 401 retry; the
  `.so` files ship in both `trees/misc/`.
- **REGISTER `regXML` fields**: `regDevSerial` + `regDevMacAddr` required on
  V2 and always sent by both builders; `regDevName`/`floorNum` optional on
  both. `Content-Type: text/xml`.
- **Digest**: realm `"Hikvision"`, no-qop MD5 — V2's verifier accepts the
  exact formula a V1 client produces (synthetic check in
  `VDM10_2.0/analysis/simulator-digest-check.json`).
- **Heartbeat**: `MESSAGE sip:sipCmd@<registrar>:5065`,
  `videoIntercomMsg` method=1/action=1, ~`0x77` s cadence, gated on 11-char
  devLongNum (`cli_heartbeat_task` @`0x00133668` vs
  `task_sipClient_HeartBeat` @`0x000e28dc`).
- **Numbering**: `T FFF UU RRR II` via the identical sscanf format
  `%01d%03d%02d%03d%02d`; `UU!=0 && RRR==0 → type 1` in both; `againdevice`,
  `ManageCenter`, `1000000000N`-series room numbers still special.
- **Method set**: REGISTER/INVITE/ACK/BYE/CANCEL/MESSAGE/INFO/OPTIONS.
- **In-dialog controls**: `locknumXML` MESSAGE fast-path and `INFO`
  `application/dtmf-relay` `Signal=<d>` bodies still handled
  (`cli_ua_task` literals).
- **`Subject:` as protocol field**, private SDP attrs
  `doorFloor/responseType/doorType/isSpecialType/isConfirm`, `s=Talk session`
  template — all still emitted (`0x00127fa4`/`0x00139fa4` literals).
- **dev_reg concept**: a registration table keyed by SIP number with
  serial/MAC/IP/expiry, persisted to the device DB and seeding a
  `ManageCenter` pseudo-row — survives the C→C++ rewrite as
  `sip_addressbook` + `room_extension_net_info`.
- **visNet TCP :8102** proprietary channel: `serverInfo`→nonce→signature
  handshake, `vns_nonce_gen`/`vns_nonce_key_fold`/`vns_md5_sign`/
  `vns_payloadXor` (cross-version-matched implementations,
  `human_readable/visnet/server.c`), cmd `0x2033` dev-table push after
  registration (`vns_registrar_sync_hook` both sides).
- **Core call flow**: INVITE→100/180/200+ACK, BYE→200, CANCEL→487; door
  calls announced then fanned out; extension calls routed direct to
  `dev_ip:5060` when resolvable (both register `100000000`-style aliases and
  keep the direct-dial path, `cli_invite`/`cli_call_doorphone` vs
  `sip_callLongNum`); global-busy semantics retained (busy/cancel/bye event
  hooks in `talkback_tables.c`).
- **Dual-NIC concept**: primary/secondary IP selection
  (`srv_primaryIpText`/`srv_secondaryIpText`) drives registration targets on
  both builds.
- **RTSP :554 media pulls** and talkback RTP ports survive in the net layer
  (`net_netService_rtpSession_*`, `ipc_talkback*`).

## Packaging deltas that are *not* protocol changes

- New rootfs members (`dhcp6c.tar.lzma`, `firmware.bin`, `io`,
  `R0-common.dtb`), one dropped (`DS17054.dtb`), and `start.sh` rewrites
  (plain commands replacing `hik_*` helpers, libcrypto/libssl symlinks,
  `log4j` start, `cam_iq`, `min_free_kbytes 4096→2048`, 65M tmpfs, `sdbg`
  modes) — **extraction-level facts** from the removed `fw2_extracted` tree;
  hashes in `extracted-file-comparison.json`; re-derive via the cramfs carve
  + 3DES recipe if needed. (`hik_rm` strings still exist inside the new
  `hicore` — the shell-script switch is packaging, not a binary change.)
- GPL list file renamed/duplicated (`Open_Source_Software_Licenses.cfg` →
  `_NEU.cfg`/`_STD.cfg`); osip/eXosip entries absent from the new lists
  while the `.so`s still ship — licensing-label change, not a stack change
  (extraction note; cfg contents not retained in-repo).

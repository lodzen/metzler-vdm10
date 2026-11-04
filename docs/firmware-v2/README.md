# Firmware internals — `hicore` (INDOOR_V2.2.100_build251203)

Topic docs built from the decompiled, human-rewritten pseudocode in
[`VDM10_2.0/`](../../VDM10_2.0/) — the V2.2.100 counterpart of
[`../firmware/`](../firmware/) (V2.0.24, based on `vdm10_1.0/`). Each claim
cites the source file, function name and original `0x…` address so it can be
traced back to the binary. Items the decompile couldn't prove are marked
**unverified**.

| Doc | Covers | Main source areas |
|---|---|---|
| [intercom-registration.md](intercom-registration.md) | REGISTER SIP2.0 vs SIP1.0-compat, digest gate, regXML, heartbeat, dev_reg, numbering | `VDM10_2.0/human_readable/sip/` |
| [doorbell-pairing.md](doorbell-pairing.md) | Indoor↔doorbell pairing, fast_regist, unlock credentials, visNet channel | `VDM10_2.0/human_readable/sip/client/`, `VDM10_2.0/human_readable/visnet/` |
| [hikconnect.md](hikconnect.md) | EZVIZ/Hik-Connect tokens, shadow_v3 alarm, QR binding, p2p | `VDM10_2.0/human_readable/ezviz/` |
| [door-call-flow.md](door-call-flow.md) | INVITE/call handling incl. SIP2.0 gates, SDP, extension fan-out, DTMF | `VDM10_2.0/human_readable/sip/`, `net/` |
| [architecture-v2.md](architecture-v2.md) | C++ sipsvrLib structure vs. the v1 C stack; sipDevSDK callback/API surface | `VDM10_2.0/human_readable/sip/server/` |

## Isolation

All V2.2.100 evidence lives under [`../../VDM10_2.0/`](../../VDM10_2.0/):
`human_readable/` (1,909 named `.c` reading-view files), `decompiled/` (raw
Ghidra dumps), `names/*.tsv`, `function_index.tsv`, `analysis/`. The saved
Ghidra project is `../../ghidra_proj_v2/hicore_v2.gpr` (program `/hicore`,
ARM v8). The authoritative registration-regression analysis is
[`../../VDM10_2.0/analysis/registration.md`](../../VDM10_2.0/analysis/registration.md);
where these docs differ from it, that report wins.

The V2.0.24 material is untouched: `../../vdm10_1.0/` + `ghidra_proj/hicore_proj`
+ [`../firmware/`](../firmware/) remain the reference for the old stack, and are
cited here only for comparison. The `fw2_extracted/` rootfs tree was removed;
re-derive it via the cramfs carve at `.dav` offset `0x6c` (3DES key
`2102034d561311120102034d561011120102034d5612111d`) — see
`VDM10_2.0/README.md` for the reproduce recipe and coverage caveats.

The simulator's own wire-level description of the same protocols is in
[../protocol.md](../protocol.md).

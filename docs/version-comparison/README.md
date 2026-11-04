# Firmware version comparison — V2.0.24 vs V2.2.100

Systematic side-by-side comparison of the two `hicore` indoor-station
firmware builds analyzed in this repo. **Read this first:**
[`registration.md`](registration.md) — the registrar was rewritten and
registration is where a SIP-1.0-era client (the simulator) actually breaks.

| Doc | Covers |
|---|---|
| [registration.md](registration.md) | REGISTER end-to-end: identity, auth, XML, response key blob, compat gate, numbering, dev_reg semantics + simulator checklist |
| [protocol-changes.md](protocol-changes.md) | On-the-wire message/format deltas: SDP codecs, User-Agent, XML vocabulary, heartbeat, SEP/auth headers, Content-Type variants |
| [subsystem-changes.md](subsystem-changes.md) | Per-subsystem implementation diff (sipServer, sipClient, visNet, ISAPI, EZVIZ, GUI, net, DB, libraries) |
| [config-and-gates.md](config-and-gates.md) | Configuration surface: the SIP1.0/2.0 byte, regPassword, registrar address fields, GUI/ISAPI toggles |
| [unchanged.md](unchanged.md) | What did *not* change — container format, ports, heartbeat, visNet handshake, core call flow |

## Caveats — read before trusting any line

- **Static analysis only.** No V2.2.100 firmware was executed and no packet
  was captured from it. Wire behavior on error paths is predicted, not
  observed (the registration analysis explicitly refuses to predict a SIP
  status code for early exits — see `VDM10_2.0/analysis/registration.md`).
- **Trees:** old-side citations use the `vdm10_1.0/` rewritten pseudocode tree
  (and `docs/firmware/*.md`, `ANALYSIS.md`); new-side citations use
  `VDM10_2.0/human_readable/` plus `VDM10_2.0/decompiled/` raw exports and
  `VDM10_2.0/analysis/`. `docs/firmware-v2/` may appear later and is *not* an
  input to this comparison.
- **Addresses are from the respective decompiles** — V1 addresses are the old
  Ghidra project (`ghidra_proj/hicore_proj`), V2 addresses are the new project
  (`ghidra_proj_v2/hicore_v2`). They are not comparable across builds.
- **Names are an analyst overlay.** On both sides the binaries are stripped;
  logical names come from self-identifying diagnostic strings and reviewed
  dataflow (`_q` suffix = uncertain). Old `FUN_*`/new `FUN_*` addresses are
  the reliable join key. New-side "reviewed behavior name" vs "diagnostic-
  recovered" vs "inferred" status is preserved where it matters.
- **Verified vs inferred** is marked per claim: *(verified)* = read in
  decompiled code or recovered literals; *(live-verified)* = also seen on the
  wire (V1 only, `ANALYSIS.md`); *(inferred)* = interpretation the static
  evidence supports but does not prove; *(extraction)* = filesystem/package
  facts from the (now removed) `fw_extracted/`/`fw2_extracted/` trees, hashes
  preserved in `VDM10_2.0/analysis/extracted-file-comparison.json`.

## The artifacts

| | Old | New |
|---|---|---|
| Image | `INDOOR_V2.0.24_build230608.dav` (13,852,822 B, sha256 `57dda41d…`) | `INDOOR_V2.2.100build251203.dav` (17,121,504 B, sha256 `3f34395f…`) |
| `hicore` | 7,747,252 B, sha256 `ce3b9751…`, ARM32 LE EABI5 (ARM v7), uClibc | 12,709,224 B, sha256 `40827802…`, ARM32 LE EABI5 (ARM v8 ISA), uClibc |
| Readable tree | `vdm10_1.0/` | `VDM10_2.0/human_readable/` (+ `VDM10_2.0/decompiled/`) |
| Function index | ~15.4k entries (`vdm10_1.0/ghidra_out/func_list.txt`) | 25,382 records — 23,963 `.text` funcs + PLT/EXTERNAL (`VDM10_2.0/function_index.tsv`, `VDM10_2.0/README.md` coverage) |

Artifact table per `VDM10_2.0/analysis/INVENTORY.md`.

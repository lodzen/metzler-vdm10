# Firmware internals — `hicore` (INDOOR_V2.0.24_build230608)

Topic docs built from the decompiled, human-rewritten pseudocode in
[`vdm10_1.0/`](../../vdm10_1.0/). Each cites the source file, function name and
original `0x…` address so a claim can be traced back to the binary. Items the
decompile couldn't prove are marked **unverified**.

| Doc | Covers | Main source areas |
|---|---|---|
| [intercom-registration.md](intercom-registration.md) | Extension registration, heartbeat, dev_reg, busy flag, call routing | `vdm10_1.0/sip/` |
| [doorbell-pairing.md](doorbell-pairing.md) | Indoor↔doorbell pairing, pre-registration, unlock credentials, visNet channel | `vdm10_1.0/sip/client/`, `vdm10_1.0/visnet/` |
| [hikconnect.md](hikconnect.md) | EZVIZ/Hik-Connect bootstrap, registration, call-state pushes | `vdm10_1.0/ezviz/`, `vdm10_1.0/ezdevsdk/` |
| [door-call-flow.md](door-call-flow.md) | Doorbell → indoor call incl. extension fan-out and the video feed | `vdm10_1.0/sip/`, `vdm10_1.0/net/`, `vdm10_1.0/gui/calls/` |

The simulator's own wire-level description of the same protocols is in
[../protocol.md](../protocol.md); this folder is the firmware's side of the
story (what `hicore` actually does, not just what the sim emulates).

Background: `../../ANALYSIS.md` (extraction + verified findings),
`../../HIKCONNECT.md` (cloud protocol details), `../../vdm10_1.0/*/README.md`
(per-area file maps and the Ghidra→vdm10_1.0 naming tables in
`../../vdm10_1.0/names/`).

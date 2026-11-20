# metzler docs

- **[architecture.md](architecture.md)** — the `src/metzler` package: module
  responsibilities, run modes (provider bridge / PBX trunk / local headset /
  outbound), threads, inbound-call dispatch, media model, Call-ID bookkeeping.
- **[protocol.md](protocol.md)** — the proprietary Hikvision intercom SIP
  dialect (registration XML, heartbeat, call classification, private SDP
  attributes, RTSP media model) plus the standard-SIP provider leg and the
  Hik-Connect cloud side effects.
- **[firmware/](firmware/)** — what the `hicore` firmware itself does:
  intercom registration, doorbell pairing, the Hik-Connect cloud channel, and
  the door→indoor call flow — documented from `vdm10_1.0/` pseudocode with
  function/address citations.
- **[diagrams/](diagrams/)** — every chart as a standalone `.mmd` file for the
  Mermaid Chart extension preview (open file → *Mermaid Chart: Preview
  Diagram*).

Related material outside this folder:

- `ANALYSIS.md` — firmware extraction/decryption and `hicore` internals.
- `HIKCONNECT.md` — the EZVIZ/Hik-Connect cloud protocol itself.
- `vdm10_1.0/` — annotated decompiled pseudocode by subsystem.
- `tests/` — behaviors pinned by unit tests (incl. wire-captured byte shapes).

## Editing the diagrams

The canonical source is the ```` ```mermaid ```` blocks inside the `.md`
files, each named by a `<!-- mermaid: name -->` marker. Regenerate the
`.mmd` files and syntax-check them:

```sh
python3 tools/check_mermaid.py
```

CI-style stale check (fail if `.mmd` files are out of sync):

```sh
python3 tools/check_mermaid.py --check
```

One-time prerequisite: `cd tools/mermaid && npm install` (mermaid + jsdom —
the same major engine version the mermaidchart extension bundles).

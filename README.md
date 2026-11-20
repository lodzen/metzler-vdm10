# vdm10-python

**Python library and indoor-station simulator for Metzler/Hikvision VDM10 SIP intercoms.**

Reverse-engineered implementation of the VDM10 private SIP dialect: registers as
a room station on the intercom's SIP registrar, receives door calls, bridges
them to a SIP provider (Linphone, Fritz!Box, Asterisk/any PBX) and back —
first answer wins, loser legs are cancelled.

## Features

- **Room-station registration** on the indoor unit's registrar (UDP 5065)
- **Door calls** — INVITE from the outdoor station, RTP media relay, DTMF unlock
- **Provider bridging** — SIP trunk to Linphone/Fritz!Box/PBX; inbound app calls
  ring the indoor stations, door calls fork to every registered endpoint
- **Local headset mode** — answer and talk directly on the machine running it
- **Media** — RTP relay, RTSP pull/talkback (PCMU), optional SRTP via `cryptography`
- **No mandatory dependencies** beyond `cryptography` — runs standalone with the
  Python stdlib at module level (`python3 src/metzler/__main__.py`)

## Install

```bash
pip install vdm10-python      # or: uv add vdm10-python
```

Requires Python ≥ 3.13.

## Run

```bash
indoor-station                # entry point, loads ./.env profile
python -m metzler             # equivalent
```

`.env` is the single profile: provider credentials, optional Fritz!Box trunk,
and local-audio toggles. See `docs/` for the full CLI surface. Offline
end-to-end check without hardware: `tools/simulate_call.py`.

## Firmware support

- **VDM10 1.0** — fully supported
- **VDM10 2.x** — registration/call flow documented, support in progress

## Documentation

| Path | Contents |
|---|---|
| `docs/protocol.md` | The Hikvision private-SIP dialect (registration, calls, media, teardown) |
| `docs/architecture.md` | Module map of `src/metzler/` |
| `docs/firmware/` | V1.0.x firmware specifics |
| `docs/firmware-v2/` | V2.x firmware specifics |
| `docs/version-comparison/` | V1 ↔ V2 protocol diff |

The reverse-engineering artifacts these docs are derived from live in
[`metzler-vdm-doc`](https://github.com/lodzen/metzler-vdm-doc). A Home Assistant
custom integration built on this library is
[`metzler-vdm10-ha`](https://github.com/lodzen/metzler-vdm10-ha).

## License

GPL-3.0-only — see [LICENSE](LICENSE).

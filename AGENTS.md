# Project notes

- Package manager: `uv`. Simulator: `src/metzler/` package (`cli.py`, `config.py`, `provider.py`,
  `sip/`, `media/`, `station/`); flat re-exports live in `__init__.py`, `__main__.py` is the
  `python -m metzler` + standalone-file entry. See `docs/architecture.md` for the module map and
  `docs/protocol.md` for the Hikvision SIP dialect.
- Run (provider/Linphone mode): `uv run indoor-station` (loads `.env` by default;
  `INDOOR_ENV=<file>` selects another profile; equivalent: `uv run python -m metzler`,
  or standalone `python3 src/metzler/__main__.py`).
  - `.env` = the single profile (secrets live only there): SIP-provider bridge + optional
    Fritz trunk; local headset mode = uncomment `INDOOR_LOCAL_AUDIO`/`INDOOR_LOCAL_MIC`.
  - UDP 5060 must be free: WSL uses mirrored networking, so a Windows SIP app (e.g. Linphone desktop) blocks it.
- Must keep working standalone (stdlib only at module level; `cryptography` is optional and only enables
  SRTP) and with a PBX trunk such as Asterisk (`--sip-host <pbx> --sip-target 6000`, no credentials = no
  REGISTER). Don't remove local headset mode or the generic trunk path.
- Tests: `uv run pytest` (118 tests, localhost only, ~5 s). Without cryptography:
  `PYTHONPATH=src uv run --isolated --no-project --with "pytest>=9.1,<9.2" python -m pytest` (SRTP tests skip).
- Never bind services to TCP 8102: it is the units' visNet push port, the main indoor connects to it.
  Offline end-to-end check without a doorbell: `uv run python tools/simulate_call.py`.
- Complexity: `complexipy src tests`; keep new/changed functions under 15 (some legacy media loops still exceed it).
- Lint + format: `uv run ruff check src tests` and `uv run ruff format src tests` (0.17.x, dev dep;
  config incl. isort rules in `pyproject.toml`; VSCode formats + sorts imports on save).
- Types: `uv run ty check` (src + tests; `unresolved-attribute` ignored in `station/` — mixin `self`).
  Standalone stays stdlib-only: `typing`/`pathlib` ok, third-party types stay test-only or TYPE_CHECKING.
- Firmware V2.0.24 (`INDOOR_V2.0.24_build230608.dav`) artifacts — consolidated into `vdm10_1.0/`:
  renamed pseudocode tree + `names/*.tsv` maps + per-dir READMEs, `vdm10_1.0/decompiled/` raw Ghidra
  dumps (incl. `recovered/`, `recovered2/`, `inferred/`, `FUNC_MAP.txt`), `vdm10_1.0/ghidra_out/`
  (`func_list.txt`, `sip_funcs.txt`, pipeline logs + `check-names.log`), `vdm10_1.0/ghidra_scripts/`
  (headless dump/recover scripts + ApplyNames + ListNames). `ghidra_proj/hicore_proj` (program
  `/hicore`, names applied) is kept; `fw_extracted/` was removed — re-derive via the cramfs carve at
  `.dav` offset 0x6c + the 3DES key recovery documented in ANALYSIS.md.
- Firmware V2.2.100 (new `INDOOR_V2.2.100build251203.dav`) artifacts — separate from the V2.0.24 set:
  `ghidra_proj_v2/hicore_v2` (Ghidra project, ARM v8, ~6.4k named funcs), `VDM10_2.0/` (canonical readable
  output: `human_readable/` renamed pseudocode, `decompiled/` raw dumps, `analysis/registration.md` =
  registration regression answer, `names/`, `function_index.tsv`, `scripts/` incl. headless scripts),
  `ghidra_out_v2/` (pipeline logs + func_list). `fw2_extracted/` and `decompiled_v2/` were folded in /
  cleaned up; re-extract via cramfs at .dav offset 0x6c + old 3DES key.
- Docs diagrams: `python3 tools/check_mermaid.py` extracts the ```mermaid blocks from docs/*.md into
  docs/diagrams/*.mmd and validates them (one-time: `cd tools/mermaid && npm install`; `--check` = stale check).
- Linphone account API: `curl -6 -H "x-api-key: $LINPHONE_API_KEY" https://subscribe.linphone.org/api/accounts/me/devices`
  (the key is bound to this host's IPv6 address, so use `-6`).

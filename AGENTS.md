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
- Tests: `uv run pytest` (117 tests, localhost only, ~5 s). Without cryptography:
  `PYTHONPATH=src uv run --isolated --no-project --with "pytest>=9.1,<9.2" python -m pytest` (SRTP tests skip).
- Complexity: `complexipy src tests`; keep new/changed functions under 15 (some legacy media loops still exceed it).
- Lint + format: `uv run ruff check src tests` and `uv run ruff format src tests` (0.17.x, dev dep;
  config incl. isort rules in `pyproject.toml`; VSCode formats + sorts imports on save).
- Types: `uv run ty check` (src + tests; `unresolved-attribute` ignored in `station/` — mixin `self`).
  Standalone stays stdlib-only: `typing`/`pathlib` ok, third-party types stay test-only or TYPE_CHECKING.
- Docs diagrams: `python3 tools/check_mermaid.py` extracts the ```mermaid blocks from docs/*.md into
  docs/diagrams/*.mmd and validates them (one-time: `cd tools/mermaid && npm install`; `--check` = stale check).
- Linphone account API: `curl -6 -H "x-api-key: $LINPHONE_API_KEY" https://subscribe.linphone.org/api/accounts/me/devices`
  (the key is bound to this host's IPv6 address, so use `-6`).

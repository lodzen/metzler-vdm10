#!/usr/bin/env python3
"""Extract ```mermaid blocks from docs/*.md into docs/diagrams/*.mmd and
syntax-validate them with the mermaid engine (tools/mermaid/validate.mjs).

Each block is named by an HTML marker on the line above it:

    <!-- mermaid: registration -->
    ```mermaid
    sequenceDiagram
        ...
    ```

Usage:
    python3 tools/check_mermaid.py           # extract + write .mmd + validate
    python3 tools/check_mermaid.py --check   # validate only, fail if .mmd stale

Requires one-time: cd tools/mermaid && npm install   (node + npm)
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
DIAGRAMS = DOCS / "diagrams"
VALIDATE = ROOT / "tools" / "mermaid" / "validate.mjs"

BLOCK = re.compile(r"<!--\s*mermaid:\s*([\w.-]+)\s*-->\s*```mermaid\n(.*?)```", re.S)


def extract():
    """md file -> [(name, source)] for every marked mermaid block"""
    found = {}
    for md in sorted(DOCS.rglob("*.md")):
        for n, (name, src) in enumerate(BLOCK.findall(md.read_text()), 1):
            name = name.removesuffix(".mmd")
            found.setdefault(name, []).append((md.name, src.strip() + "\n"))
    return found


def main():
    check_only = "--check" in sys.argv
    blocks = extract()
    if not blocks:
        sys.exit("no <!-- mermaid: name --> blocks found in docs/*.md")
    DIAGRAMS.mkdir(exist_ok=True)

    stale, files = [], []
    for name, copies in blocks.items():
        if len(copies) > 1:
            sys.exit(f"diagram name {name!r} used in {[md for md, _ in copies]}")
        path = DIAGRAMS / f"{name}.mmd"
        src = copies[0][1]
        if path.exists() and path.read_text() != src:
            stale.append(path)
            if not check_only:
                path.write_text(src)
        elif not check_only or not path.exists():
            path.write_text(src)
        files.append(str(path))
    if stale and check_only:
        sys.exit("stale .mmd files (re-run without --check): " + ", ".join(str(p) for p in stale))

    try:
        r = subprocess.run(["node", str(VALIDATE), *files])
    except FileNotFoundError:
        sys.exit("node not found — install node, or `cd tools/mermaid && npm install` first")
    if r.returncode:
        sys.exit(r.returncode)
    print(f"all {len(files)} diagrams valid")


if __name__ == "__main__":
    main()

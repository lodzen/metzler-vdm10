#!/usr/bin/env python3
"""Dump the ISAPI URI routing table from the extracted hicore ELF.

The binary path below is recreated by re-extracting the .dav (cramfs @0x6c,
see ANALYSIS.md) when fw_extracted/ is absent.

The hicore .data section holds a tree of 0x68-byte route records:
    +0x00  char*  name        ("/X" rows are per-table terminators)
    +0x04  void*  handler     (leaf dispatcher stub, 0 on dir rows)
    +0x08  u32    (unused/0 in V2.0.24)
    +0x0c  void*  childTbl    (nested segment table, 0 on leaves)
    +0x10  u32    (1 in all observed rows)
    +0x14  u32    method mask (bit0=GET bit1=POST? bit2=PUT — inferred)
    +0x18..0x68  zero padding

Usage: python3 tools/dump_isapi_table.py [--tsv]
"""
import struct
import sys

BINARY = "fw_extracted/trees/vistalk/hicore"
STRIDE = 0x68
ROOT_END = 0x756D34          # "/ISAPI" terminator row of the root table
ROOT_GROUP = "/ISAPI"


def load(path):
    data = open(path, "rb").read()
    phoff = struct.unpack_from("<I", data, 0x1C)[0]
    phentsize, phnum = struct.unpack_from("<HH", data, 0x2A)
    loads = []
    for i in range(phnum):
        p_type, p_offset, p_vaddr, _, p_filesz, _, _, _ = struct.unpack_from(
            "<IIIIIIII", data, phoff + i * phentsize)
        if p_type == 1:
            loads.append((p_offset, p_vaddr, p_filesz))
    return data, loads


def make_helpers(data, loads):
    def v2o(v):
        for o, va, fs in loads:
            if va <= v < va + fs:
                return o + (v - va)
        return None

    def cstr(v):
        o = v2o(v)
        if o is None:
            return None
        end = data.index(b"\0", o)
        s = data[o:end]
        if 0 < len(s) < 64 and all(32 <= b < 127 for b in s):
            return s.decode()
        return None

    def rec(va):
        o = v2o(va)
        if o is None:
            return None
        return struct.unpack_from("<IIIIII", data, o)

    return v2o, cstr, rec


def walk_table(tbl_va, prefix, out, cstr, rec, seen):
    """rows are contiguous 0x68 records until the "/<name>" terminator row"""
    if tbl_va in seen:
        print(f"# cycle: table 0x{tbl_va:x} revisited under {prefix}", file=sys.stderr)
        return
    seen = seen | {tbl_va}                        # per-branch copy
    va = tbl_va
    while True:
        r = rec(va)
        if r is None:
            print(f"# WARN unreadable record @0x{va:x} under {prefix}", file=sys.stderr)
            return
        name_v, handler, _f08, child, f10, f14 = r
        name = cstr(name_v)
        if name is None:
            return                                     # end of mapped data
        if name.startswith("/"):
            return                                     # terminator row
        path = f"{prefix}/{name}"
        out.append((path, va, handler, child, f10, f14))
        if child:
            # shared tables (e.g. the generic /index/param blocks) legitimately
            # appear under several parents - only true self-reference is a cycle
            walk_table(child, path, out, cstr, rec, seen | {tbl_va})
        va += STRIDE


def main():
    data, loads = load(BINARY)
    v2o, cstr, rec = make_helpers(data, loads)

    # root table: contiguous records ending at the "/ISAPI" terminator
    root_start = ROOT_END
    while True:
        r = rec(root_start - STRIDE)
        if r is None or cstr(r[0]) is None:
            break
        root_start -= STRIDE

    out = []
    walk_table(root_start, ROOT_GROUP, out, cstr, rec, set())

    methods = {0: "-", 1: "GET", 2: "POST?", 3: "GET+?", 4: "PUT", 5: "GET+PUT?"}
    for path, va, handler, child, f10, f14 in out:
        print(f"{methods.get(f14, f'mask{f14}'):8} {path:70} "
          f"h=0x{handler:06x}{' +' if child else ''}")


if __name__ == "__main__":
    main()

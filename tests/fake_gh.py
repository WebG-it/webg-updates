#!/usr/bin/env python3
"""A stand-in for `gh` for the tests of tools/release.py: releases live in the folder FAKE_GH_DIR (nothing leaves the PC).
Understands: release view <tag> --repo R | release create <tag> <file> --repo R ... | release download <tag> --repo R --pattern P --dir D.
FAKE_GH_CORRUPT=1 makes `download` give back different bytes."""
import os
import shutil
import sys

d = os.environ["FAKE_GH_DIR"]
a = sys.argv[1:]
if a[:2] == ["release", "view"]:
    sys.exit(0 if os.path.isdir(os.path.join(d, a[2])) else 1)
if a[:2] == ["release", "create"]:
    tag, f = a[2], a[3]
    os.makedirs(os.path.join(d, tag))
    shutil.copy(f, os.path.join(d, tag, os.path.basename(f)))
    open(os.path.join(d, "calls.log"), "a").write(" ".join(a) + "\n")
    sys.exit(0)
if a[:2] == ["release", "download"]:
    tag = a[2]
    pat = a[a.index("--pattern") + 1]
    out = a[a.index("--dir") + 1]
    src = os.path.join(d, tag, pat)
    if not os.path.isfile(src):
        print("no such asset", file=sys.stderr)
        sys.exit(1)
    data = open(src, "rb").read()
    if os.environ.get("FAKE_GH_CORRUPT"):
        data = data + b"x"
    open(os.path.join(out, pat), "wb").write(data)
    sys.exit(0)
print("unsupported: " + " ".join(a), file=sys.stderr)
sys.exit(2)

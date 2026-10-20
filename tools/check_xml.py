#!/usr/bin/env python3
"""Checks the update XML files of this repository (run by tools/release.py and by the tests; run it by hand before a push).

  python -I tools/check_xml.py            checks webgpay.xml and webgautomations.xml
  python -I tools/check_xml.py file.xml   checks that one

What it checks, per <update>: the fields Joomla needs, a download address on https://updates.webg.it/<product>/<version>.zip that
agrees with <version>, a 64-hex sha256, the target platform Joomla 5 and 6, php_minimum, and that nothing in the file looks like a secret
(a key, a token) or a private repository download address.
Exit code 0 = fine, 1 = problems (printed).
"""
import os
import re
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PRODUCTS = {"webgpay": "pkg_webgpay", "webgautomations": "pkg_webgautomations"}
HOST = "https://updates.webg.it/"
SECRETISH = re.compile(r"(ghp_|github_pat_|wgk_[A-Za-z0-9_-]{20,}|key=|token|secret|password|api[_-]?key)", re.I)
REQUIRED = ["name", "description", "element", "type", "client", "version", "downloads", "sha256", "targetplatform", "php_minimum"]


def check(path):
    problems = []
    slug = os.path.splitext(os.path.basename(path))[0]
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        return ["%s: not well-formed XML: %s" % (path, e)]
    if root.tag != "updates":
        problems.append("root element must be <updates>")
    ups = root.findall("update")
    if len(ups) != 1:
        problems.append("expected exactly one <update>, found %d" % len(ups))
    for u in ups:
        for r in REQUIRED:
            if u.find(r) is None:
                problems.append("missing <%s>" % r)
        g = lambda t: (u.findtext(t) or "").strip()
        if slug not in PRODUCTS:
            problems.append("unknown product file name %r" % slug)
        elif g("element") != PRODUCTS[slug]:
            problems.append("<element> must be %s" % PRODUCTS[slug])
        if g("type") != "package":
            problems.append("<type> must be package")
        v = g("version")
        if not re.fullmatch(r"\d+\.\d+\.\d+", v):
            problems.append("bad <version> %r" % v)
        d = u.find("downloads/downloadurl")
        url = (d.text or "").strip() if d is not None else ""
        want = "%s%s/%s.zip" % (HOST, slug, v)
        if url != want:
            problems.append("downloadurl is %r, expected %r" % (url, want))
        if d is not None and (d.get("type") != "full" or d.get("format") != "zip"):
            problems.append('downloadurl needs type="full" format="zip"')
        if not re.fullmatch(r"[0-9a-f]{64}", g("sha256")):
            problems.append("sha256 must be 64 lowercase hex characters")
        tp = u.find("targetplatform")
        if tp is not None and (tp.get("name") != "joomla" or not re.fullmatch(r"\(5\|6\)\\\.\[0-9\]\+", tp.get("version", ""))):
            problems.append("targetplatform must be joomla (5|6)\\.[0-9]+")
        if not re.fullmatch(r"\d+\.\d+", g("php_minimum")):
            problems.append("bad <php_minimum>")
    # a comment may explain "?key=", the data may not carry anything key-like
    body = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    m = SECRETISH.search(body)
    if m:
        problems.append("looks like a secret or a key in the data: %r" % m.group(0))
    if re.search(r"raw\.githubusercontent\.com/WebG-it/(webgpay|webgautomations)/", body) or "api.github.com" in body:
        problems.append("a download address on a private repository")
    return ["%s: %s" % (os.path.basename(path), p) for p in problems]


def main(argv):
    files = argv or [os.path.join(ROOT, s + ".xml") for s in PRODUCTS]
    bad = []
    for f in files:
        bad += check(f)
    for b in bad:
        print("FAIL " + b)
    if not bad:
        print("OK %d file(s)" % len(files))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

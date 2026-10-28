#!/usr/bin/env python3
"""Publishes a version of a paid package: private GitHub Release with the zip, then the public update XML.

  python -I tools/release.py --product webgpay --zip ../webgpay-git/dist/pkg_webgpay-0.2.2.zip [--notes "text" | --notes-file f]
                             [--changelog CHANGELOG.md | --no-changelog] [--target main] [--dry-run] [--push]

What it does, in this order (it stops at the first problem, and the XML is touched LAST: it never announces a zip that is not there):
  1. checks the zip: the package manifest inside says the same version as the file name, declares THE update server of this channel
     and <dlid prefix="key=">, and nothing in the zip looks like a token or a key;
  2. checks that the version is newer than the one in <product>.xml and that the Release does not exist yet (a Release is never overwritten);
  3. `gh release create v<version> <zip>` on the PRIVATE repo of the product (the gh of the PC: its token can write releases);
  4. downloads the asset back and compares its sha256 with the local zip;
  5. rewrites version, download address and sha256 in <product>.xml (and adds its <changelogurl>), writes <product>-changelog.xml from the CHANGELOG.md of the
     product (only the "In breve" paragraph of each version, in the format Joomla reads: <changelogs><changelog>), runs check_xml.py, commits (git add + commit in THIS repo);
  6. with --push, pushes. Without it, it tells you to: until the push, no site sees the new version.

Nothing secret is read or printed: `gh` uses its own login. WEBG_GH can name another gh command (the tests use a stand-in).
"""
import argparse
import hashlib
import os
import re
import shlex
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_xml  # noqa: E402

PRODUCTS = {
    "webgpay": {"name": "WebG Pay", "repo": "WebG-it/webgpay", "element": "pkg_webgpay"},
    "webgautomations": {"name": "WebG Automations", "repo": "WebG-it/webgautomations", "element": "pkg_webgautomations"},
}
CHANNEL = "https://raw.githubusercontent.com/WebG-it/webg-updates/main/%s.xml"
CHANGELOG_URL = "https://raw.githubusercontent.com/WebG-it/webg-updates/main/%s-changelog.xml"   # public, read by Joomla with no key, next to <product>.xml
WITH_CHANGELOG = ("webgpay",)                                                                    # the products whose repository keeps a CHANGELOG.md in the agreed format
SECRETISH = re.compile(rb"(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|wgk_[A-Za-z0-9_-]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|sk_live_[A-Za-z0-9]{10,}|AKIA[0-9A-Z]{16})")


def die(msg):
    print("STOP: " + msg)
    sys.exit(1)


def gh(*args, check=True):
    exe = shlex.split(os.environ.get("WEBG_GH", "gh"))
    r = subprocess.run([*exe, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        die("gh %s failed: %s" % (" ".join(args[:2]), (r.stderr or r.stdout).strip()[:400]))
    return r


def sha256_of(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def vtuple(v):
    return tuple(int(x) for x in v.split("."))


def check_zip(path, slug, free=False, changelog=False):
    p = PRODUCTS[slug]
    if not os.path.isfile(path):
        die("zip not found: " + path)
    m = re.fullmatch(r"%s-(\d+\.\d+\.\d+)\.zip" % re.escape(p["element"]), os.path.basename(path))
    if not m:
        die("the file must be named %s-<x.y.z>.zip" % p["element"])
    version = m.group(1)
    with zipfile.ZipFile(path) as z:
        bad = z.testzip()
        if bad:
            die("broken zip entry " + bad)
        if p["element"] + ".xml" not in z.namelist():
            die("%s.xml is not at the root of the zip" % p["element"])
        manifest = z.read(p["element"] + ".xml").decode("utf-8")
        for n in z.namelist():
            if "\\" in n:
                die("backslash in a zip entry name: " + n)
            data = z.read(n)
            if SECRETISH.search(data):
                die("something that looks like a token or a key is inside the zip (%s)" % n)
    mv = re.search(r"<version>([^<]+)</version>", manifest)
    if not mv or mv.group(1).strip() != version:
        die("the manifest says version %s, the file name says %s" % (mv.group(1) if mv else "none", version))
    if CHANNEL % slug not in manifest:
        die("the manifest does not declare the update server " + CHANNEL % slug)
    if changelog and "<changelogurl>%s</changelogurl>" % (CHANGELOG_URL % slug) not in manifest:
        die("the manifest does not declare <changelogurl>%s</changelogurl>: Joomla would show no changelog" % (CHANGELOG_URL % slug))
    if not free and not re.search(r'<dlid\s+prefix="key="\s+suffix=""\s*/>', manifest):
        die('the manifest has no <dlid prefix="key=" suffix=""/>: Joomla would not ask for the download key')
    return version


def current_version(xml_path):
    with open(xml_path, encoding="utf-8") as f:
        return re.search(r"<version>([^<]+)</version>", f.read()).group(1).strip()


def _plain(t):
    """One line of plain text out of a Markdown paragraph: no bold, no code marks, links reduced to their text."""
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = t.replace("**", "").replace("`", "")
    return re.sub(r"\s+", " ", t).strip()


def parse_changelog(md):
    """[(version, text)] newest first: the "In breve" paragraph of each `## x.y.z` section of CHANGELOG.md (the technical details of the section are left out)."""
    out = []
    for m in re.finditer(r"^## (\d+\.\d+\.\d+)\b[^\n]*\n(.*?)(?=^## |\Z)", md, flags=re.S | re.M):
        para = re.search(r"^\*\*In breve[^\n]*?\*\*(.*?)(?:\n\s*\n|\Z)", m.group(2), flags=re.S | re.M)
        text = _plain(para.group(1)) if para else ""
        if text:
            out.append((m.group(1), text))
    return out


def changelog_xml(slug, entries):
    # the text goes in CDATA: Joomla's reader makes one list item per piece of character data, and splits a line at every entity or non-ASCII character otherwise
    p = PRODUCTS[slug]
    lines = ['<?xml version="1.0" encoding="utf-8"?>',
             "<!-- Changelog of %s for the Update screen of Joomla. Written by tools/release.py from CHANGELOG.md: do not edit by hand. -->" % p["name"], "<changelogs>"]
    for v, text in entries:
        lines += ["    <changelog>", "        <element>%s</element>" % p["element"], "        <type>package</type>", "        <version>%s</version>" % v,
                  "        <security></security>", "        <fix></fix>", "        <language></language>", "        <addition></addition>",
                  "        <change>", "            <item><![CDATA[%s]]></item>" % text.replace("]]>", "]]]]><![CDATA[>"), "        </change>", "        <remove></remove>", "        <note></note>", "    </changelog>"]
    lines.append("</changelogs>")
    return "\n".join(lines) + "\n"


def build_changelog(slug, md_path, version):
    """The text of <product>-changelog.xml, or a stop when CHANGELOG.md has no "In breve" for the version that goes out."""
    if not md_path or not os.path.isfile(md_path):
        die("CHANGELOG.md not found (%s): pass --changelog <file>, or --no-changelog to publish without one" % md_path)
    with open(md_path, encoding="utf-8") as f:
        entries = parse_changelog(f.read())
    if version not in [v for v, _ in entries]:
        die('CHANGELOG.md has no "In breve" paragraph for version %s: write it before the release' % version)
    return changelog_xml(slug, entries)


def write_xml(xml_path, slug, version, sha, changelog=False):
    with open(xml_path, encoding="utf-8") as f:
        x = f.read()
    x = re.sub(r"<version>[^<]+</version>", "<version>%s</version>" % version, x, count=1)
    x = re.sub(r"(https://updates\.webg\.it/%s/)[0-9.]+(\.zip)" % slug, r"\g<1>%s\g<2>" % version, x)
    x = re.sub(r"<sha256>[^<]+</sha256>", "<sha256>%s</sha256>" % sha, x, count=1)
    if changelog and "<changelogurl>" not in x:
        x = re.sub(r"([ \t]*)(<infourl[^\n]*</infourl>)\n", lambda m: "%s%s\n%s<changelogurl>%s</changelogurl>\n" % (m.group(1), m.group(2), m.group(1), CHANGELOG_URL % slug), x, count=1)
    with open(xml_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(x)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--product", required=True, choices=sorted(PRODUCTS))
    ap.add_argument("--zip", required=True)
    ap.add_argument("--notes", default="")
    ap.add_argument("--notes-file")
    ap.add_argument("--target", help="branch or commit of the private repo the tag points to (default: its default branch)")
    ap.add_argument("--changelog", help="the CHANGELOG.md of the product (default: the one above the folder of the zip, ../CHANGELOG.md)")
    ap.add_argument("--no-changelog", action="store_true", help="publish without the changelog file (Joomla then shows no changelog for this version)")
    ap.add_argument("--free", action="store_true", help="free period: the product is served without a key (the Worker has free:true), so the manifest need not ask for one")
    ap.add_argument("--dry-run", action="store_true", help="checks only, changes nothing")
    ap.add_argument("--push", action="store_true", help="push this repository after the commit")
    ap.add_argument("--xml-dir", default=ROOT, help="folder of the XML files (the tests use a copy)")
    a = ap.parse_args(argv)

    slug, p = a.product, PRODUCTS[a.product]
    xml_path = os.path.join(a.xml_dir, slug + ".xml")
    with_cl = slug in WITH_CHANGELOG and not a.no_changelog
    version = check_zip(a.zip, slug, a.free, with_cl)
    sha = sha256_of(a.zip)
    cur = current_version(xml_path)
    if vtuple(version) <= vtuple(cur):
        die("version %s is not newer than the one published in %s.xml (%s)" % (version, slug, cur))
    tag = "v" + version
    if gh("release", "view", tag, "--repo", p["repo"], check=False).returncode == 0:
        die("the Release %s already exists on %s: a Release is never overwritten" % (tag, p["repo"]))
    print("OK   zip %s  version %s  sha256 %s" % (os.path.basename(a.zip), version, sha))
    print("OK   newer than %s; no Release %s yet on %s" % (cur, tag, p["repo"]))
    cl_text = None
    if with_cl:
        cl_text = build_changelog(slug, a.changelog or os.path.join(os.path.dirname(os.path.abspath(a.zip)), "..", "CHANGELOG.md"), version)
        print("OK   changelog of %s: %d version(s), the one that goes out included" % (slug, cl_text.count("<changelog>")))
    if a.dry_run:
        print("DRY-RUN: nothing created. Without --dry-run: Release %s, then %s.xml -> %s" % (tag, slug, version))
        return 0

    if a.notes_file:
        with open(a.notes_file, encoding="utf-8") as f:
            notes = f.read()
    else:
        notes = a.notes or "%s %s" % (p["name"], version)
    args = ["release", "create", tag, a.zip, "--repo", p["repo"], "--title", "%s %s" % (p["name"], version), "--notes", notes]
    if a.target:
        args += ["--target", a.target]
    gh(*args)
    print("DONE Release %s created on %s" % (tag, p["repo"]))

    with tempfile.TemporaryDirectory() as td:
        gh("release", "download", tag, "--repo", p["repo"], "--pattern", os.path.basename(a.zip), "--dir", td)
        back = os.path.join(td, os.path.basename(a.zip))
        if not os.path.isfile(back) or sha256_of(back) != sha:
            die("the zip downloaded back from the Release is not the one uploaded: the XML was NOT touched")
    print("OK   downloaded back: same sha256")

    write_xml(xml_path, slug, version, sha, with_cl)
    cl_path = os.path.join(a.xml_dir, slug + "-changelog.xml")
    if cl_text is not None:
        with open(cl_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(cl_text)
    problems = check_xml.check(xml_path) + (check_xml.check_changelog(cl_path) if cl_text is not None else [])
    if problems:
        die("the XML does not pass check_xml.py after the update: " + "; ".join(problems))
    print("DONE %s.xml -> %s" % (slug, version) + ("; %s-changelog.xml written" % slug if cl_text is not None else ""))

    if os.path.abspath(a.xml_dir) == ROOT:
        files = [slug + ".xml"] + ([slug + "-changelog.xml"] if cl_text is not None else [])
        subprocess.run(["git", "-C", ROOT, "add", *files], check=True)
        subprocess.run(["git", "-C", ROOT, "commit", "-m", "%s %s" % (p["name"], version), "--", *files], check=True, capture_output=True)
        print("DONE committed")
        if a.push:
            subprocess.run(["git", "-C", ROOT, "push"], check=True)
            print("DONE pushed: sites see %s from now on" % version)
        else:
            print("TODO git push   (until then no site sees %s)" % version)
    return 0


if __name__ == "__main__":
    sys.exit(main())

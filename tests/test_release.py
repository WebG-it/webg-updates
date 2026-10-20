#!/usr/bin/env python3
"""Tests of tools/check_xml.py and tools/release.py with a stand-in `gh`.   python -I tests/test_release.py"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import check_xml  # noqa: E402

GOOD = """<?xml version="1.0" encoding="utf-8"?>
<extension type="package" method="upgrade">
    <name>WebG Pay</name>
    <version>%(v)s</version>
    <updateservers>
        <server type="extension" name="WebG Pay">%(server)s</server>
    </updateservers>
    %(dlid)s
</extension>
"""
SERVER = "https://raw.githubusercontent.com/WebG-it/webg-updates/main/webgpay.xml"
DLID = '<dlid prefix="key=" suffix=""/>'


def make_zip(path, v="0.2.2", server=SERVER, dlid=DLID, extra=None, mv=None):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("pkg_webgpay.xml", GOOD % {"v": mv or v, "server": server, "dlid": dlid})
        z.writestr("script.php", "<?php // nothing\n")
        for k, val in (extra or {}).items():
            z.writestr(k, val)


class T(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.xmls = os.path.join(self.td, "xml")
        os.makedirs(self.xmls)
        for f in ("webgpay.xml", "webgautomations.xml"):
            shutil.copy(os.path.join(ROOT, f), self.xmls)
        self.gh = os.path.join(self.td, "gh")
        os.makedirs(self.gh)
        self.env = dict(os.environ, FAKE_GH_DIR=self.gh, WEBG_GH='"%s" -I "%s"' % (sys.executable, os.path.join(HERE, "fake_gh.py")))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def run_release(self, zippath, *extra, env=None):
        return subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tools", "release.py"), "--product", "webgpay", "--zip", zippath,
                               "--xml-dir", self.xmls, *extra], capture_output=True, text=True, env=env or self.env)

    def zip(self, name="pkg_webgpay-0.2.2.zip", **kw):
        p = os.path.join(self.td, name)
        make_zip(p, **kw)
        return p

    def xml(self):
        with open(os.path.join(self.xmls, "webgpay.xml"), encoding="utf-8") as f:
            return f.read()

    def test_shipped_xml_pass_the_checker(self):
        self.assertEqual(check_xml.main([]), 0)

    def test_happy_path(self):
        z = self.zip()
        r = self.run_release(z)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(z, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        x = self.xml()
        self.assertIn("<version>0.2.2</version>", x)
        self.assertIn("https://updates.webg.it/webgpay/0.2.2.zip", x)
        self.assertIn("<sha256>%s</sha256>" % sha, x)
        self.assertEqual(check_xml.check(os.path.join(self.xmls, "webgpay.xml")), [])
        self.assertTrue(os.path.isfile(os.path.join(self.gh, "v0.2.2", "pkg_webgpay-0.2.2.zip")))
        with open(os.path.join(self.gh, "calls.log")) as f:
            self.assertIn("--repo WebG-it/webgpay", f.read())
        with open(os.path.join(self.xmls, "webgautomations.xml")) as a, open(os.path.join(ROOT, "webgautomations.xml")) as b:
            self.assertEqual(a.read(), b.read())

    def test_dry_run_changes_nothing(self):
        before = self.xml()
        r = self.run_release(self.zip(), "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.xml(), before)
        self.assertFalse(os.path.exists(os.path.join(self.gh, "v0.2.2")))

    def refuses(self, zippath, why, *extra):
        before = self.xml()
        r = self.run_release(zippath, *extra)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(why, r.stdout)
        self.assertEqual(self.xml(), before, "the XML must stay untouched")
        return r

    def test_refusals(self):
        self.refuses(self.zip(dlid=""), "no <dlid")
        self.refuses(self.zip(server="https://raw.githubusercontent.com/WebG-it/webgpay/main/updates-pkg.xml"), "does not declare the update server")
        self.refuses(self.zip(mv="0.2.3"), "manifest says version")
        self.refuses(self.zip(name="pkg_webgpay-0.0.0.zip", v="0.0.0"), "not newer")
        p = os.path.join(self.xmls, "webgpay.xml")
        with open(p, encoding="utf-8") as f:
            t = f.read()
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(t.replace("<version>0.0.0</version>", "<version>0.3.0</version>").replace("0.0.0.zip", "0.3.0.zip"))
        self.refuses(self.zip(), "not newer")
        self.refuses(self.zip(name="pkg_webgpay-0.3.0.zip", v="0.3.0"), "not newer")
        self.refuses(self.zip(extra={"x.php": "<?php $t='ghp_" + "A" * 30 + "';"}), "looks like a token")
        self.refuses(self.zip(name="other.zip"), "must be named")

    def test_existing_release_is_never_overwritten(self):
        os.makedirs(os.path.join(self.gh, "v0.2.2"))
        self.refuses(self.zip(), "already exists")

    def test_corrupt_upload_leaves_the_xml_alone(self):
        env = dict(self.env, FAKE_GH_CORRUPT="1")
        before = self.xml()
        r = self.run_release(self.zip(), env=env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("not the one uploaded", r.stdout)
        self.assertEqual(self.xml(), before)

    def test_checker_catches_bad_files(self):
        def chk(text, name="webgpay.xml"):
            p = os.path.join(self.td, name)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            return check_xml.check(p)
        good = self.xml()
        self.assertTrue(chk(good.replace("0.0.0.zip", "9.9.9.zip")))                                  # address and version disagree
        self.assertTrue(chk(good.replace("https://updates.webg.it/", "https://raw.githubusercontent.com/WebG-it/webgpay/main/dist/")))
        self.assertTrue(chk(good.replace("<sha256>0", "<sha256>G")))
        self.assertTrue(chk(good.replace("<tag>stable</tag>", "<tag>stable</tag><x>wgk_" + "a" * 40 + "</x>")))
        self.assertTrue(chk(good.replace("(5|6)", "(4|5)")))
        self.assertTrue(chk(good.replace("<element>pkg_webgpay", "<element>pkg_other")))
        self.assertTrue(chk("<updates><update>", "webgpay.xml"))


if __name__ == "__main__":
    unittest.main(verbosity=1)

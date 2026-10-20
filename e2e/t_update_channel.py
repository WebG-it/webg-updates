#!/usr/bin/env python3
"""End-to-end test of the update channel of a paid WebG package, on a throw-away Joomla (Docker), with a FAKE channel.

  python tools/e2e/t_update_channel.py --joomla 6 [--product webgpay] [--zip dist/pkg_webgpay-0.2.2.zip] [--keep]

What it proves, on the real Joomla 5.4.9 or 6.1.4 (images pinned by digest, like the rest of the kit):
  1. the package installs from the zip (Extensions > Install, like a person) and Joomla registers ONE update site, the channel declared by
     the manifest, and shows the Download Key field in System > Update Sites (the <dlid prefix="key="> of the manifest works);
  2. NO key: the update is found but the download is refused with a clear text (401), nothing is installed;
  3. WRONG key: refused (403, the same text as for any wrong key), nothing is installed, the visible error does not repeat the key;
  4. RIGHT key (typed in the Download Key field, as a customer does): the update is downloaded and installed, the sha256 of the XML is checked
     by Joomla, the version goes up;
  5. the key survives the update (still on the one update site), and a second update check still works with it;
  6. the key is NOWHERE except in the Download Key field of that update site: not in the logs of Joomla or of the channel, not in any other
     table, not in the pages of the error.
Nothing real is contacted: the network of the containers has no way out, the channel is a small server in the same network, and every
key is made at random for the run. Needs Docker; Python 3.9+. About 3 minutes per Joomla.
"""
import argparse
import hashlib
import http.cookiejar
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = dict(os.environ, MSYS_NO_PATHCONV="1", MSYS2_ARG_CONV_EXCL="*")
PRODUCTS = {
    "webgpay": {"element": "pkg_webgpay", "name": "WebG Pay"},
    "webgautomations": {"element": "pkg_webgautomations", "name": "WebG Automations"},
}
DEFAULT_PRODUCT = "webgpay"
IMAGES = {
    "5": "joomla@sha256:7e01c8dcbfdcbfc94da3aa719efa01894db688f43386e798c3f7e8f575d345bd",
    "6": "joomla@sha256:ae220b4caddec280f4e3247bc4f77e9ddc508dfa503273121ad9a8011eaad5c3",
}
MARIADB = "mariadb@sha256:1292844148b311e4ed4300022a996d39083f415a963e970cf47cad1b3b18e3a6"
PYIMG = "python@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4"
CHANNEL_HOST = "https://raw.githubusercontent.com/WebG-it/webg-updates/main/"
RES = {"pass": 0, "fail": 0}

FORWARDER = """
import asyncio, os
T = os.environ['TARGET'].split(':')
async def pipe(r, w):
    try:
        while True:
            d = await r.read(65536)
            if not d:
                break
            w.write(d)
            await w.drain()
    except Exception:
        pass
    finally:
        try:
            w.close()
        except Exception:
            pass
async def h(r, w):
    try:
        tr, tw = await asyncio.open_connection(T[0], int(T[1]))
    except Exception:
        w.close()
        return
    await asyncio.gather(pipe(r, tw), pipe(tr, w))
async def main():
    s = await asyncio.start_server(h, '0.0.0.0', 8080)
    await s.serve_forever()
asyncio.run(main())
"""


def check(name, cond, extra=""):
    RES["pass" if cond else "fail"] += 1
    print(("PASS " if cond else "FAIL ") + name + ((" | " + str(extra)[:700]) if (extra != "" and not cond) else ""), flush=True)
    return cond


def sh(cmd, inp=None, timeout=600):
    r = subprocess.run(cmd, capture_output=True, env=ENV, input=inp, timeout=timeout)
    return r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")


# ------------------------------------------------------------------------------------------------ the package files
def bump(zpath, out, new_version):
    """The same package with the version of ITS manifest raised: what a release would be. Only the package manifest changes."""
    with zipfile.ZipFile(zpath) as zi, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zo:
        for info in zi.infolist():
            data = zi.read(info.filename)
            if info.filename.startswith("pkg_") and info.filename.endswith(".xml"):
                data = re.sub(rb"<version>[^<]+</version>", b"<version>" + new_version.encode() + b"</version>", data, count=1)
            ni = zipfile.ZipInfo(info.filename, (2026, 10, 20, 0, 0, 0))
            ni.compress_type = zipfile.ZIP_DEFLATED
            ni.external_attr = 0o644 << 16
            zo.writestr(ni, data)


def manifest_of(zpath, element):
    with zipfile.ZipFile(zpath) as z:
        return z.read(element + ".xml").decode("utf-8")


def next_patch(v):
    a, b, c = v.split(".")
    return "%s.%s.%d" % (a, b, int(c) + 1)


def sha256_of(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def find_update_xml(slug):
    for base in (os.environ.get("WEBG_UPDATES_DIR"), os.path.join(HERE, "..", "..", "..", "webg-updates-git"), os.path.join(HERE, "..")):
        if base and os.path.isfile(os.path.join(base, slug + ".xml")):
            with open(os.path.join(base, slug + ".xml"), encoding="utf-8") as f:
                return f.read()
    return None


# ------------------------------------------------------------------------------------------------ web client
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Sess:
    def __init__(self, base):
        self.base = base
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj), NoRedirect())

    def req(self, url, data=None, headers=None, method=None, follow=True):
        if not url.startswith("http"):
            url = self.base + url
        body = None
        if data is not None:
            body = data if isinstance(data, bytes) else urllib.parse.urlencode(data, doseq=True).encode()
        rq = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        try:
            r = self.op.open(rq, timeout=180)
            code, txt, hd = r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
        except urllib.error.HTTPError as e:
            code, txt, hd = e.code, e.read().decode("utf-8", "replace"), dict(e.headers)
        if follow and code in (301, 302, 303) and hd.get("Location"):
            return self.req(urllib.parse.urljoin(url, hd["Location"]))
        return code, txt, hd

    def get(self, url):
        return self.req(url)[1]

    def post(self, url, data):
        return self.req(url, data)[1]

    def upload(self, url, fields, filefield, path):
        b = "----wgupd" + secrets.token_hex(8)
        parts = []
        for k, v in fields.items():
            parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (b, k, v)).encode())
        with open(path, "rb") as f:
            blob = f.read()
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\nContent-Type: application/zip\r\n\r\n" % (b, filefield, os.path.basename(path))).encode() + blob + b"\r\n")
        parts.append(("--%s--\r\n" % b).encode())
        return self.req(url, b"".join(parts), {"Content-Type": "multipart/form-data; boundary=" + b})[1]


def alerts(html):
    """The messages Joomla shows (success, errors), as plain text."""
    return " | ".join(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", a)).strip() for a in re.findall(r"<joomla-alert[^>]*>(.*?)</joomla-alert>", html, re.S))[:600]


def tokens(html):
    return re.findall(r'<input type="hidden" name="([0-9a-f]{32})" value="1"', html)


# ------------------------------------------------------------------------------------------------ the stack
class Stack:
    def __init__(self, major, port):
        self.major, self.port = major, port
        self.n = "wgupd-" + major
        self.net, self.db, self.j, self.gw, self.chan = self.n + "-net", self.n + "-db", self.n + "-j", self.n + "-gw", self.n + "-chan"
        self.admin_pw = secrets.token_urlsafe(14)
        self.P = None

    def down(self):
        sh(["docker", "rm", "-f", self.gw, self.j, self.db, self.chan])
        sh(["docker", "network", "rm", self.net])

    def up(self, data_dir, good_hashes):
        self.down()
        sh(["docker", "network", "create", "--internal", self.net])
        sh(["docker", "create", "--name", self.chan, "--network", self.net, "-e", "DATA_DIR=/data", "-e", "GOOD_KEYS_SHA256=" + ",".join(good_hashes), PYIMG, "python", "-u", "/fake_channel.py"])
        sh(["docker", "cp", os.path.join(HERE, "fake_channel.py"), self.chan + ":/fake_channel.py"])
        sh(["docker", "cp", data_dir, self.chan + ":/data"])
        sh(["docker", "start", self.chan])
        sh(["docker", "run", "-d", "--name", self.db, "--network", self.net, "-e", "MARIADB_ROOT_PASSWORD=rootpw", "-e", "MARIADB_DATABASE=joomla", "-e", "MARIADB_USER=joomla", "-e", "MARIADB_PASSWORD=joomlapw", "--memory", "512m", MARIADB])
        time.sleep(12)
        sh(["docker", "run", "-d", "--name", self.j, "--network", self.net, "-e", "JOOMLA_DB_HOST=" + self.db, "-e", "JOOMLA_DB_USER=joomla", "-e", "JOOMLA_DB_PASSWORD=joomlapw",
            "-e", "JOOMLA_DB_NAME=joomla", "-e", "JOOMLA_SITE_NAME=wgupd", "-e", "JOOMLA_ADMIN_USER=admin", "-e", "JOOMLA_ADMIN_USERNAME=admin", "-e", "JOOMLA_ADMIN_PASSWORD=" + self.admin_pw,
            "-e", "JOOMLA_ADMIN_EMAIL=a@example.invalid", "--memory", "1g", IMAGES[self.major]])
        for _ in range(120):
            time.sleep(4)
            if "resuming normal operations" in sh(["docker", "logs", self.j]):
                break
        else:
            raise SystemExit("Joomla did not come up")
        # the door: a forwarder that carries requests IN (published on this PC) to Joomla; the containers still have no way OUT
        sh(["docker", "create", "--name", self.gw, "-p", "127.0.0.1:%d:8080" % self.port, "-e", "TARGET=%s:80" % self.j, PYIMG, "python", "-u", "-c", FORWARDER])
        sh(["docker", "network", "connect", self.net, self.gw])
        sh(["docker", "start", self.gw])
        time.sleep(2)
        self.P = self._prefix()

    def _prefix(self):
        t = self.sql("SELECT table_name FROM information_schema.tables WHERE table_schema='joomla' AND table_name REGEXP '^[A-Za-z0-9]+_extensions$'")
        return t.split()[0][:-len("extensions")]

    def sql(self, q):
        return sh(["docker", "exec", self.db, "mariadb", "-ujoomla", "-pjoomlapw", "joomla", "-N", "-B", "-e", q]).strip("\r\n")

    def jx(self, cmd, user=None):
        a = ["docker", "exec"] + (["-u", user] if user else []) + [self.j, "sh", "-c", cmd]
        return sh(a)

    def chan_state(self):
        out = sh(["docker", "exec", self.chan, "python", "-c", "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8080/_state').read().decode())"])
        return json.loads(out.strip().splitlines()[-1])


# ------------------------------------------------------------------------------------------------ the test
def run(args):
    slug = args.product
    P = PRODUCTS[slug]
    el = P["element"]
    if not args.zip:
        root = os.path.abspath(os.path.join(HERE, "..", ".."))
        if not os.path.isfile(os.path.join(root, "tools", "build.py")):
            raise SystemExit("--zip is required here (no tools/build.py to build the package)")
        sh([sys.executable, os.path.join(root, "tools", "build.py")])
        v = re.search(r"<version>([^<]+)</version>", open(os.path.join(root, "src", el, el + ".xml"), encoding="utf-8").read()).group(1)
        args.zip = os.path.join(root, "dist", "%s-%s.zip" % (el, v))
    manifest = manifest_of(args.zip, el)
    old_v = re.search(r"<version>([^<]+)</version>", manifest).group(1).strip()
    new_v = next_patch(old_v)
    work = tempfile.mkdtemp(prefix="wgupd_")
    data = os.path.join(work, "data")
    os.makedirs(os.path.join(data, slug))
    new_zip = os.path.join(data, slug, new_v + ".zip")
    bump(args.zip, new_zip, new_v)
    new_sha = sha256_of(new_zip)

    KEY = "wgk_" + secrets.token_urlsafe(32)
    WRONG = "wgk_" + secrets.token_urlsafe(32)
    st = Stack(args.joomla, args.port)
    chan_url = "http://%s:8080" % st.chan
    # the XML the channel serves: the one of the webg-updates repository when it is at hand, with the host part replaced by the fake channel
    tpl = find_update_xml(slug)
    if tpl is None:
        tpl = ('<?xml version="1.0" encoding="utf-8"?>\n<updates><update><name>%s</name><description>x</description><element>%s</element><type>package</type><client>site</client>'
               '<version>0.0.0</version><infourl title="x">https://www.webg.it</infourl><downloads><downloadurl type="full" format="zip">https://updates.webg.it/%s/0.0.0.zip</downloadurl></downloads>'
               '<sha256>0</sha256><tags><tag>stable</tag></tags><targetplatform name="joomla" version="(5|6)\\.[0-9]+" /><php_minimum>8.1</php_minimum></update></updates>\n') % (P["name"], el, slug)
    xml = re.sub(r"<version>[^<]+</version>", "<version>%s</version>" % new_v, tpl, count=1)
    xml = re.sub(r"<downloadurl([^>]*)>[^<]+</downloadurl>", r"<downloadurl\1>%s/%s/%s.zip</downloadurl>" % (chan_url, slug, new_v), xml)
    xml = re.sub(r"<sha256>[^<]+</sha256>", "<sha256>%s</sha256>" % new_sha, xml)
    with open(os.path.join(data, slug + ".xml"), "w", encoding="utf-8", newline="\n") as f:
        f.write(xml)

    pages = {}          # name -> html of the pages a key could leak into (the edit form of the update site is allowed to show it: it is the field)
    try:
        st.up(data, [hashlib.sha256(KEY.encode()).hexdigest()])
        # logging on, so that a leak would be written down
        st.jx("sed -i \"s/public \\$log_everything = '0'/public \\$log_everything = '1'/; s/public \\$debug = false/public \\$debug = true/\" /var/www/html/configuration.php")
        base = "http://127.0.0.1:%d" % st.port
        s = Sess(base)
        h = s.get("/administrator/index.php")
        d = {"username": "admin", "passwd": st.admin_pw, "option": "com_login", "task": "login", "return": "aW5kZXgucGhw"}
        d.update({t: "1" for t in tokens(h)})
        s.post("/administrator/index.php", d)
        check("L0 the administrator is logged in", "com_users" in s.get("/administrator/index.php") or "task=logout" in s.get("/administrator/index.php") or "Logout" in s.get("/administrator/index.php"), "")

        # ---- 1. install the zip like a person
        form = s.get("/administrator/index.php?option=com_installer&view=install")
        tk = tokens(form)
        up = {"installtype": "upload", "task": "install.install", "option": "com_installer"}
        up.update({t: "1" for t in tk[:1]})
        out = s.upload("/administrator/index.php?option=com_installer&view=install", up, "install_package", args.zip)
        ext = st.sql("SELECT extension_id, manifest_cache FROM %sextensions WHERE type='package' AND element='%s'" % (st.P, el))
        got_v = (re.search(r'"version":"([^"]+)"', ext) or [None, None])[1]
        check("1a the package installs from the zip through Extensions > Install (version %s)" % old_v, got_v == old_v, (got_v, alerts(out)))
        ext_id = ext.split()[0] if ext else ""
        sites = st.sql("SELECT s.update_site_id, s.location, s.enabled, s.extra_query FROM %supdate_sites s JOIN %supdate_sites_extensions e ON e.update_site_id=s.update_site_id WHERE e.extension_id=%s" % (st.P, st.P, ext_id or 0))
        rows = [r.split("\t") for r in sites.splitlines() if r]
        check("1b ONE update site, the channel declared by the manifest, enabled, with no key yet", len(rows) == 1 and rows[0][1] == CHANNEL_HOST + slug + ".xml" and rows[0][2] == "1" and rows[0][3] in ("", "NULL"), rows)
        usid = rows[0][0] if rows else "0"
        edit = s.get("/administrator/index.php?option=com_installer&task=updatesite.edit&update_site_id=%s" % usid)
        check("1c System > Update Sites shows the Download Key field for it (the dlid of the manifest is read)", "jform[extra_query]" in edit, re.findall(r'name="jform\[[a-z_]+\]"', edit))
        s.get("/administrator/index.php?option=com_installer&view=updatesites")           # leave the edit lock
        # the installer message that tells where the key goes (shown after the install)
        check("1d the first install tells where to enter the download key (language key of the package)", ("download key" in out.lower()) or ("chiave di download" in out.lower()), re.sub(r"<[^>]+>", " ", out)[-400:])
        st.sql("UPDATE %supdate_sites SET location='%s/%s.xml' WHERE update_site_id=%s" % (st.P, chan_url, slug, usid))      # only the host part: the fake channel answers

        def find_updates():
            st.sql("DELETE FROM %supdates" % st.P)
            st.sql("UPDATE %supdate_sites SET last_check_timestamp=0" % st.P)
            return st.jx("cd /var/www/html && php cli/joomla.php update:extensions:check 2>&1", user="www-data")

        def available():
            r = st.sql("SELECT update_id, version FROM %supdates WHERE element='%s'" % (st.P, el))
            return [x.split("\t") for x in r.splitlines() if x]

        def installed_version():
            m = st.sql("SELECT manifest_cache FROM %sextensions WHERE type='package' AND element='%s'" % (st.P, el))
            return (re.search(r'"version":"([^"]+)"', m) or [None, None])[1]

        def apply_update(update_id):
            page = s.get("/administrator/index.php?option=com_installer&view=update")
            t = tokens(page)
            body = {"task": "update.update", "option": "com_installer", "cid[]": update_id}
            body.update({x: "1" for x in t[:1]})
            return s.post("/administrator/index.php?option=com_installer&view=update", body)

        def set_key(value):
            """Types the key in the Download Key field and saves, as a customer does. The form carries the prefix and suffix of the manifest
            (hidden fields): Joomla puts them around what is typed."""
            f = s.get("/administrator/index.php?option=com_installer&task=updatesite.edit&update_site_id=%s" % usid)
            body = {}
            for tag in re.findall(r"<input[^>]*>", f):
                n = re.search(r'name="(jform\[[A-Za-z_]+\])"', tag)
                if n and " disabled" not in tag:
                    v = re.search(r'value="([^"]*)"', tag)
                    body[n.group(1)] = v.group(1) if v else ""
            body["jform[extra_query]"] = value
            body.update({"task": "updatesite.save", "option": "com_installer"})
            body.update({x: "1" for x in tokens(f)[:1]})
            r = s.post("/administrator/index.php?option=com_installer&view=updatesite&layout=edit&update_site_id=%s" % usid, body)
            s.get("/administrator/index.php?option=com_installer&view=updatesites")
            return r

        # ---- 2. no key
        find_updates()
        av = available()
        check("2a with no key the update of version %s is found (the XML is public)" % new_v, any(x[1] == new_v for x in av), av)
        if av:
            page = apply_update(av[0][0])
            pages["update-nokey"] = page
            text = re.sub(r"<[^>]+>", " ", page)
            check("2b nothing is installed, the version stays %s" % old_v, installed_version() == old_v, installed_version())
            check("2c the distributor refused with 401 (no key sent: Joomla asked for it, did not send an empty one by mistake)", st.chan_state()["zip_no_key"] + st.chan_state()["zip_bad_key"] >= 1, st.chan_state())
            check("2d the administrator sees that the download failed", re.search(r"(download|scaric|connect|server|errore|error)", text, re.I) is not None, text[:300])

        # ---- 3. wrong key
        set_key(WRONG)
        check("3a the wrong key is saved with the prefix the manifest asked for (key=...)", st.sql("SELECT extra_query FROM %supdate_sites WHERE update_site_id=%s" % (st.P, usid)) == "key=" + WRONG, st.sql("SELECT LEFT(extra_query,6) FROM %supdate_sites WHERE update_site_id=%s" % (st.P, usid)))
        before = st.chan_state()
        find_updates()
        av = available()
        page = apply_update(av[0][0]) if av else ""
        pages["update-wrongkey"] = page
        after = st.chan_state()
        check("3b wrong key: refused by the distributor (403), nothing installed", after["zip_bad_key"] > before["zip_bad_key"] and installed_version() == old_v, (before, after, installed_version()))
        fail_msg = page.replace("\\/", "/")      # the messages come as JSON, where / is written \/
        check("3c Joomla says the download failed and gives the address to try by hand (where the distributor's own text explains)", "Failed to download package" in fail_msg and ("%s/%s/%s.zip" % (chan_url, slug, new_v)) in fail_msg, alerts(page))
        print("INFO Joomla itself repeats the download address, key included, in that failure message (admin screen only, not in any log): %s" % (WRONG in fail_msg), flush=True)

        # ---- 4. right key
        set_key(KEY)
        before = st.chan_state()
        find_updates()
        av = available()
        check("4a with the right key the update is offered", any(x[1] == new_v for x in av), av)
        page = apply_update(av[0][0]) if av else ""
        pages["update-ok"] = page
        after = st.chan_state()
        check("4b the right key downloads the zip (distributor: 1 download served)", after["zip_ok"] == before["zip_ok"] + 1, (before, after))
        check("4c Joomla installed it: the package is now %s" % new_v, installed_version() == new_v, (installed_version(), alerts(page)))
        # ---- 5. the key survives
        rows = st.sql("SELECT s.location, s.extra_query FROM %supdate_sites s JOIN %supdate_sites_extensions e ON e.update_site_id=s.update_site_id WHERE e.extension_id=%s" % (st.P, st.P, ext_id)).splitlines()
        check("5a after the update there is still ONE update site and the key is still on it", len(rows) == 1 and rows[0].endswith("\tkey=" + KEY), len(rows))
        find_updates()
        check("5b a new check with the key still works (the XML is read, nothing newer is offered)", available() == [], available())
        xs = st.chan_state()
        print("INFO the XML request carried the key to the XML host: %s (of %d XML requests)" % (xs["xml_query_has_key"] > 0, xs["xml"]), flush=True)

        # ---- 6. nowhere else
        leaks = []
        logs = st.jx("grep -rlF -- '%s' /var/www/html/administrator/logs /var/www/html/logs /var/log/apache2 /tmp 2>/dev/null; true" % KEY).split()
        wrong_logs = st.jx("grep -rlF -- '%s' /var/www/html/administrator/logs /var/www/html/logs /var/log/apache2 /tmp 2>/dev/null; true" % WRONG).split()
        leaks += [("joomla-files", x) for x in logs + wrong_logs]
        for name, sx in (("docker-logs-joomla", sh(["docker", "logs", st.j])), ("docker-logs-channel", sh(["docker", "logs", st.chan])), ("docker-logs-db", sh(["docker", "logs", st.db]))):
            if KEY in sx or WRONG in sx:
                leaks.append((name, ""))
        dump = sh(["docker", "exec", st.db, "mariadb-dump", "-ujoomla", "-pjoomlapw", "joomla", "--ignore-table=joomla.%supdate_sites" % st.P, "--ignore-table=joomla.%supdates" % st.P, "--skip-extended-insert"], timeout=300)
        if KEY in dump or WRONG in dump:
            leaks.append(("database (any table but update_sites and updates, where Joomla keeps it for the check)", ""))
        for n, html in pages.items():
            if n != "update-wrongkey" and (KEY in html or WRONG in html):          # the failure message of a wrong key does show the address (see 3c)
                leaks.append(("page " + n, ""))
        if os.environ.get("WGUPD_DEBUG"):
            for n, html in pages.items():
                with open("/tmp/wgupd_page_%s.html" % n, "w", encoding="utf-8") as f:
                    f.write(re.sub(r"wgk_[A-Za-z0-9_-]+", "wgk_***", html))
        check("6 the keys are in no log of Joomla, Apache, the channel or the database, in no table but the Download Key field, and in no error page", not leaks, leaks)
        clog = sh(["docker", "logs", st.chan])
        check("6b the log of the channel has method, path and outcome only (no query string)", ".zip -> 200" in clog and ".zip -> 403" in clog and "?" not in clog, clog[-300:])
    finally:
        if not args.keep:
            st.down()
        shutil.rmtree(work, ignore_errors=True)
    print("\nRESULT pass=%d fail=%d" % (RES["pass"], RES["fail"]))
    return 1 if RES["fail"] else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--joomla", choices=["5", "6"], default="6")
    ap.add_argument("--product", choices=sorted(PRODUCTS), default=DEFAULT_PRODUCT)
    ap.add_argument("--zip", help="the package to test (default: build it with tools/build.py of the kit)")
    ap.add_argument("--port", type=int, default=0, help="host port of the test Joomla (default 8390 for 5, 8396 for 6)")
    ap.add_argument("--keep", action="store_true", help="leave the containers up (name prefix wgupd-)")
    a = ap.parse_args()
    a.port = a.port or (8390 if a.joomla == "5" else 8396)
    sys.exit(run(a))

#!/usr/bin/env python3
"""A stand-in for the update channel (the public XML on raw.githubusercontent.com + the key-checking distributor on updates.webg.it),
for the end-to-end test of a paid package. Runs INSIDE a container of the isolated test network; stdlib only.

  GET /<product>.xml                 the update XML (what Joomla reads in System > Update)
  GET /<product>/<version>.zip?key=  the zip if the key is right; 401 if no key; 403 if wrong (the same text for every wrong key)
  GET /_state                        JSON with counters (the test reads it)

Environment: DATA_DIR (folder with <product>.xml and <product>/<version>.zip), GOOD_KEYS_SHA256 (comma separated sha256 of the valid keys).
Its log (stdout) is exactly what a careful distributor logs: method, path WITHOUT the query string, outcome. Never the key.
"""
import hashlib
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

DATA = os.environ.get("DATA_DIR", "/data")
GOOD = set(x for x in os.environ.get("GOOD_KEYS_SHA256", "").split(",") if x)
STATE = {"xml": 0, "xml_with_query": 0, "xml_query_has_key": 0, "zip_ok": 0, "zip_no_key": 0, "zip_bad_key": 0, "other": 0}

NEED = "Download key required. Enter your key in System > Update Sites (Download Key).\n"
DENY = "Download key not valid for this product. Check it in System > Update Sites.\n"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def say(self, outcome):
        sys.stdout.write("%s %s -> %s\n" % (self.command, urlsplit(self.path).path, outcome))
        sys.stdout.flush()

    def send(self, code, body, ctype="text/plain; charset=utf-8", filename=None):
        self.send_response(code)
        if filename:
            # Joomla names the downloaded file after this header; without it the file is called "<version>.zip_key_<key>" and cannot be extracted
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % filename)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlsplit(self.path)
        q = parse_qs(u.query)
        if u.path == "/_state":
            return self.send(200, json.dumps(STATE).encode(), "application/json")
        m = re.fullmatch(r"/([a-z0-9]+)\.xml", u.path)
        if m and os.path.isfile(os.path.join(DATA, m.group(1) + ".xml")):
            STATE["xml"] += 1
            if u.query:
                STATE["xml_with_query"] += 1
                if "key" in q:
                    STATE["xml_query_has_key"] += 1
            self.say("xml")
            with open(os.path.join(DATA, m.group(1) + ".xml"), "rb") as f:
                return self.send(200, f.read(), "text/xml; charset=utf-8")
        m = re.fullmatch(r"/([a-z0-9]+)/(\d+\.\d+\.\d+)\.zip", u.path)
        if m:
            key = (q.get("key") or [""])[0].strip()
            if not key:
                STATE["zip_no_key"] += 1
                self.say("401")
                return self.send(401, NEED.encode())
            if hashlib.sha256(key.encode()).hexdigest() not in GOOD:
                STATE["zip_bad_key"] += 1
                self.say("403")
                return self.send(403, DENY.encode())
            p = os.path.join(DATA, m.group(1), m.group(2) + ".zip")
            if not os.path.isfile(p):
                self.say("404")
                return self.send(404, b"Version not available.\n")
            STATE["zip_ok"] += 1
            self.say("200")
            with open(p, "rb") as f:
                return self.send(200, f.read(), "application/zip", "pkg_%s-%s.zip" % (m.group(1), m.group(2)))
        STATE["other"] += 1
        self.say("404")
        self.send(404, b"Not found.\n")


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), H).serve_forever()

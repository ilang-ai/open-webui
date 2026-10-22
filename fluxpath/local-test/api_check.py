# -*- coding: utf-8 -*-
"""Checks the judgment filter inside the running local instance through the API, as the test user
(no browser session, so nothing can be confirmed or asked: M1/M2 pass, everything else is not sent).

  python local-test/api_check.py
"""
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "http://127.0.0.1:3000"
seed = json.load(open(os.path.join(ROOT, ".local", "owui-test-seed.json"), encoding="utf-8"))


def call(path, data, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=json.dumps(data).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw[:300]


status, out = call("/api/v1/auths/signin", {"email": seed["user"]["email"], "password": seed["user"]["password"]})
if status != 200:
    sys.exit("test user signin failed: %s" % status)
token, role = out["token"], out.get("role")
print("signed in as test user, role:", role)

failed = 0
for marker, want in (("M1", "reply"), ("M2", "reply"), ("M3", "not sent"), ("M5", "not sent"), ("M6", "blocked"),
                     ("M7", "blocked"), ("M8", "blocked"), ("503", "reply")):
    text = "api check [%s]" % marker
    status, out = call("/api/chat/completions", {"model": "mock-model", "stream": False,
                                                 "messages": [{"role": "user", "content": text}]}, token)
    if isinstance(out, dict) and out.get("choices"):
        got, shown = "reply", out["choices"][0]["message"]["content"]
    else:
        shown = json.dumps(out, ensure_ascii=False) if not isinstance(out, str) else out
        got = "blocked" if "Blocked." in shown else "not sent" if "Not sent" in shown else "other"
    ok = got == want
    failed += not ok
    print("%s  [%s] http %s -> %s   %s" % ("PASS" if ok else "FAIL", marker, status, got, shown[:150]))
sys.exit(1 if failed else 0)

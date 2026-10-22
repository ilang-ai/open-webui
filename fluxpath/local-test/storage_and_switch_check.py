# -*- coding: utf-8 -*-
"""Two checks on the running local instance, as the test user:

1. Nothing the test user said is on the server: no chat rows for the user, and none of the test
   phrases anywhere in the database files.
2. The user's own switch works: with the filter's user valve `enabled` off, a message the mock judge
   would stop goes through; switched back on, it is stopped again.

  python local-test/storage_and_switch_check.py "phrase one" "phrase two"
"""
import glob
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "http://127.0.0.1:3000"
DATA = os.path.join(ROOT, ".local", "owui-data")
FILTER_ID = "fluxpath_judgment"
seed = json.load(open(os.path.join(ROOT, ".local", "owui-test-seed.json"), encoding="utf-8"))


def call(method, path, data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=json.dumps(data).encode("utf-8") if data is not None else None,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw[:300]


failed = 0


def check(name, ok, detail=""):
    global failed
    failed += not ok
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if detail and not ok else ""))


status, out = call("POST", "/api/v1/auths/signin", {"email": seed["user"]["email"], "password": seed["user"]["password"]})
token, user_id = out["token"], out["id"]

# 1 storage
status, chats = call("GET", "/api/v1/chats/?page=1", token=token)
check("chat list of the test user is empty", status == 200 and chats == [], chats)
db = sqlite3.connect(os.path.join(DATA, "webui.db"))
rows = db.execute("select count(*) from chat where user_id = ?", (user_id,)).fetchone()[0]
check("no chat rows for the test user in the database", rows == 0, rows)
tables = [r[0] for r in db.execute("select name from sqlite_master where type='table'")]
db.close()
phrases = [p.encode("utf-8") for p in sys.argv[1:]]
files = [f for f in glob.glob(os.path.join(DATA, "**", "*"), recursive=True) if os.path.isfile(f)]
hits = []
for path in files:
    blob = open(path, "rb").read()
    hits += [(os.path.relpath(path, DATA), p.decode()) for p in phrases if p in blob]
check("none of %d test phrases in any of %d files under the data directory" % (len(phrases), len(files)), not hits, hits)
print("      tables: %d, files scanned: %s" % (len(tables), ", ".join(sorted(os.path.relpath(f, DATA) for f in files))[:300]))

# 2 the user's switch
status, spec = call("GET", "/api/v1/functions/id/%s/valves/user/spec" % FILTER_ID, token=token)
check("user valve spec offers 'enabled', default on", status == 200 and spec["properties"]["enabled"].get("default") is True, spec)


def send(text):
    s, o = call("POST", "/api/chat/completions", {"model": "mock-model", "stream": False,
                                                  "messages": [{"role": "user", "content": text}]}, token)
    return "reply" if isinstance(o, dict) and o.get("choices") else "stopped"


check("switch on: [M8] is stopped", send("switch check one [M8]") == "stopped")
status, out = call("POST", "/api/v1/functions/id/%s/valves/user/update" % FILTER_ID, {"enabled": False}, token)
check("user can switch the judgment off", status == 200 and out.get("enabled") is False, out)
check("switch off: [M8] goes through", send("switch check two [M8]") == "reply")
status, out = call("POST", "/api/v1/functions/id/%s/valves/user/update" % FILTER_ID, {"enabled": True}, token)
check("switch back on: [M8] is stopped again", status == 200 and send("switch check three [M8]") == "stopped")
sys.exit(1 if failed else 0)

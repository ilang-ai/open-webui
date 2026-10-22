# -*- coding: utf-8 -*-
"""Seeds the local test instance (http://127.0.0.1:3000): a test admin, a test user, and the FluxPath
judgment filter installed, switched on for every model and pointed at the mock judge.

Test credentials are generated here and kept in .local/owui-test-seed.json. They exist only for this
local instance. Safe to run again: it signs in with the stored credentials and updates the filter.

  python local-test/seed.py
"""
import glob
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BASE = "http://127.0.0.1:3000"
SEED = os.path.join(ROOT, ".local", "owui-test-seed.json")
FILTER = sorted(glob.glob(os.path.join(ROOT, "judgment-filter", "fluxpath_judgment_filter_v*.py")))[-1]
FILTER_ID = "fluxpath_judgment"
JUDGE_URL = "http://127.0.0.1:7861"
JUDGE_TOKEN = ""
# --real-judge: point the filter at the production judgment Space, with the owner's token from the
# local secrets file. Every message then costs one real judgment call.
if "--real-judge" in sys.argv:
    JUDGE_URL = "https://ilangai-ilang-judge-mvp.hf.space"
    with open(os.path.expanduser(r"~\.claude\secrets\hf-ilang.env"), encoding="utf-8") as f:
        for line in f:
            if line.startswith("HF_TOKEN="):
                JUDGE_TOKEN = line.split("=", 1)[1].strip().strip('"')


def call(method, path, data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=json.dumps(data).encode("utf-8") if data is not None else None,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode("utf-8")
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw[:300]


for _ in range(120):
    try:
        if call("GET", "/health")[0] == 200:
            break
    except Exception:  # noqa: BLE001
        pass
    time.sleep(1)
else:
    sys.exit("local instance is not answering on %s" % BASE)

seed = json.load(open(SEED, encoding="utf-8")) if os.path.exists(SEED) else None
if seed is None:
    seed = {"admin": {"name": "Local Admin", "email": "admin@fluxpath.test", "password": secrets.token_urlsafe(15)},
            "user": {"name": "Local User", "email": "user@fluxpath.test", "password": secrets.token_urlsafe(15)}}
    status, out = call("POST", "/api/v1/auths/signup", seed["admin"])
    if status != 200:
        sys.exit("admin signup failed: %s %s" % (status, out))
    with open(SEED, "w", encoding="utf-8") as f:
        json.dump(seed, f, indent=2)
    print("created admin", seed["admin"]["email"])

status, out = call("POST", "/api/v1/auths/signin", {"email": seed["admin"]["email"], "password": seed["admin"]["password"]})
if status != 200:
    sys.exit("admin signin failed: %s %s" % (status, out))
token = out["token"]
print("admin role:", out.get("role"))

# Open WebUI switches public sign-up off once the first admin exists; a public app turns it back on.
status, cfg = call("GET", "/api/v1/auths/admin/config", token=token)
if status == 200 and not cfg.get("ENABLE_SIGNUP"):
    cfg["ENABLE_SIGNUP"] = True
    status, cfg = call("POST", "/api/v1/auths/admin/config", cfg, token)
print("public sign-up:", cfg.get("ENABLE_SIGNUP") if isinstance(cfg, dict) else status)

status, out = call("POST", "/api/v1/auths/add", dict(seed["user"], role="user"), token)
print("test user:", "created" if status == 200 else "exists or refused (%s)" % status)

content = open(FILTER, encoding="utf-8").read()
form = {"id": FILTER_ID, "name": "FluxPath Judgment Layer", "content": content,
        "meta": {"description": "Asks the iLang judgment endpoint before every model request.", "manifest": {}}}
status, existing = call("GET", "/api/v1/functions/id/%s" % FILTER_ID, token=token)
if status == 200 and existing:
    status, fn = call("POST", "/api/v1/functions/id/%s/update" % FILTER_ID, form, token)
    print("filter updated:", status)
else:
    status, fn = call("POST", "/api/v1/functions/create", form, token)
    print("filter created:", status, "" if status == 200 else fn)
    fn = fn if isinstance(fn, dict) else {}

status, fn = call("GET", "/api/v1/functions/id/%s" % FILTER_ID, token=token)
if not fn.get("is_active"):
    status, fn = call("POST", "/api/v1/functions/id/%s/toggle" % FILTER_ID, token=token)
if not fn.get("is_global"):
    status, fn = call("POST", "/api/v1/functions/id/%s/toggle/global" % FILTER_ID, token=token)
print("filter type=%s active=%s global=%s" % (fn.get("type"), fn.get("is_active"), fn.get("is_global")))

status, valves = call("POST", "/api/v1/functions/id/%s/valves/update" % FILTER_ID,
                      {"judge_url": JUDGE_URL, "judge_token": JUDGE_TOKEN}, token)
print("valves:", status, {k: v for k, v in (valves or {}).items() if k != "judge_token"})

status, models = call("GET", "/api/models", token=token)
print("models:", [m.get("id") for m in (models or {}).get("data", [])] if status == 200 else status)
print("source:", os.path.basename(FILTER))

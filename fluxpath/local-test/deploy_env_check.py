# -*- coding: utf-8 -*-
"""Checks that deploy/fluxpath.env.example does what it says, against the local instance started
with it (launch config fluxpath-owui-local-deploy-env).

  python local-test/deploy_env_check.py
"""
import json
import os
import secrets
import sqlite3
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "http://127.0.0.1:3000"
SEED = os.path.join(ROOT, ".local", "owui-test-seed.json")
seed = json.load(open(SEED, encoding="utf-8"))


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


# a fresh sign-up lands as an ordinary user straight away
if "signup" not in seed:
    seed["signup"] = {"name": "Signup Check", "email": "signup-check@fluxpath.test", "password": secrets.token_urlsafe(15)}
    status, out = call("POST", "/api/v1/auths/signup", seed["signup"])
    check("public sign-up is open", status == 200, out)
    with open(SEED, "w", encoding="utf-8") as f:
        json.dump(seed, f, indent=2)
status, out = call("POST", "/api/v1/auths/signin", {"email": seed["signup"]["email"], "password": seed["signup"]["password"]})
check("a new sign-up is an ordinary user, not pending", status == 200 and out.get("role") == "user", out if status != 200 else out.get("role"))
token, user_id = out["token"], out["id"]

status, config = call("GET", "/api/config", token=token)
features = config.get("features", {})
check("name is shown as 'FluxPath (Open WebUI)'", config.get("name") == "FluxPath (Open WebUI)", config.get("name"))
for key, want in (("enable_direct_connections", True), ("enable_signup", True), ("enable_api_keys", False),
                  ("enable_notes", False), ("enable_channels", False), ("enable_memories", False),
                  ("enable_message_rating", False), ("enable_community_sharing", False),
                  ("enable_admin_chat_access", False), ("enable_admin_export", False),
                  ("enable_autocomplete_generation", False), ("enable_version_update_check", False)):
    check("%s is %s" % (key, want), features.get(key) is want, features.get(key))

status, perms = call("GET", "/api/v1/users/permissions", token=token)
chat, feat = perms.get("chat", {}), perms.get("features", {})
check("temporary chat is enforced for users", chat.get("temporary_enforced") is True, chat.get("temporary_enforced"))
check("file upload is off for users", chat.get("file_upload") is False, chat.get("file_upload"))
for key in ("memories", "notes", "channels"):
    check("feature '%s' is off for users" % key, feat.get(key) is False, feat.get(key))

# a conversation in the browser's temporary mode leaves nothing behind; the API path stores nothing either
status, out = call("POST", "/api/chat/completions", {"model": "mock-model", "stream": False,
                                                     "messages": [{"role": "user", "content": "deploy env check phrase [M1]"}]}, token)
check("a user can chat", status == 200 and bool(out.get("choices")), out)
db = sqlite3.connect(os.path.join(ROOT, ".local", "owui-data", "webui.db"))
check("no chat rows for that user", db.execute("select count(*) from chat where user_id = ?", (user_id,)).fetchone()[0] == 0)
db.close()
for name in ("webui.db", "webui.db-wal"):
    path = os.path.join(ROOT, ".local", "owui-data", name)
    if os.path.exists(path):
        check("phrase not in %s" % name, b"deploy env check phrase" not in open(path, "rb").read())

# the admin cannot read users' chats or export the database
status, out = call("POST", "/api/v1/auths/signin", {"email": seed["admin"]["email"], "password": seed["admin"]["password"]})
admin = out["token"]
status, out = call("GET", "/api/v1/chats/list/user/%s" % user_id, token=admin)
check("admin cannot list a user's chats", status in (401, 403), (status, out))
status, out = call("GET", "/api/v1/utils/db/download", token=admin)
check("admin cannot download the database", status in (401, 403), (status, str(out)[:80]))
sys.exit(1 if failed else 0)

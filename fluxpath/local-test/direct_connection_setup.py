# -*- coding: utf-8 -*-
"""Gives the local test user a connection of their own (a "direct connection"): the second mock
model on 127.0.0.1:7863, called from the browser rather than through the server. This is the
mechanism a first-login wizard would fill in for a real key.

  python local-test/direct_connection_setup.py
"""
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "http://127.0.0.1:3000"
seed = json.load(open(os.path.join(ROOT, ".local", "owui-test-seed.json"), encoding="utf-8"))


def call(method, path, data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=json.dumps(data).encode("utf-8") if data is not None else None,
                                 headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8") or "null")


token = call("POST", "/api/v1/auths/signin", {"email": seed["user"]["email"], "password": seed["user"]["password"]})["token"]
saved = call("POST", "/api/v1/users/user/settings/update", {"ui": {"directConnections": {
    "OPENAI_API_BASE_URLS": ["http://127.0.0.1:7863/v1"],
    "OPENAI_API_KEYS": ["mock-key"],
    "OPENAI_API_CONFIGS": {"0": {"enable": True}},
}}}, token)
stored = call("GET", "/api/v1/users/user/settings?raw=true", token=token)
conn = (stored.get("ui") or {}).get("directConnections") or {}
print("direct connection saved for the test user:", conn.get("OPENAI_API_BASE_URLS"))
print("the key is kept in the user's settings on the server:", conn.get("OPENAI_API_KEYS") == ["mock-key"])
sys.exit(0 if conn else 1)

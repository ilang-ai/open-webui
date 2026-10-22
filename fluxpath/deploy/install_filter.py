# -*- coding: utf-8 -*-
"""Installs (or updates) the FluxPath judgment filter on a running FluxPath instance and points it
at the judgment endpoint. Standard library only; run it on the server against the loopback port.

  python3 install_filter.py --base http://127.0.0.1:8080 --env fluxpath.env \
      --filter fluxpath_judgment_filter_v0.1.0_2026-10-02.py \
      --judge-url https://<space>.hf.space --judge-token-file judge.token

The admin signs in with WEBUI_ADMIN_EMAIL / WEBUI_ADMIN_PASSWORD from the env file. Safe to rerun.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

FILTER_ID = "fluxpath_judgment"


def env_values(path):
    vals = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip('"')
    return vals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    ap.add_argument("--env", required=True)
    ap.add_argument("--filter", required=True)
    ap.add_argument("--judge-url", required=True)
    ap.add_argument("--judge-token-file", default="")
    a = ap.parse_args()

    def call(method, path, data=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(a.base + path, data=json.dumps(data).encode("utf-8") if data is not None else None,
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "null")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")[:300]

    env = env_values(a.env)
    status, out = call("POST", "/api/v1/auths/signin", {"email": env["WEBUI_ADMIN_EMAIL"], "password": env["WEBUI_ADMIN_PASSWORD"]})
    if status != 200 or out.get("role") != "admin":
        sys.exit("admin signin failed: %s %s" % (status, out))
    token = out["token"]

    # Open WebUI switches public sign-up off once the first admin exists; a public app turns it back on.
    status, cfg = call("GET", "/api/v1/auths/admin/config", token=token)
    if status == 200 and not cfg.get("ENABLE_SIGNUP"):
        cfg["ENABLE_SIGNUP"] = True
        status, cfg = call("POST", "/api/v1/auths/admin/config", cfg, token)
    print("public sign-up:", cfg.get("ENABLE_SIGNUP") if isinstance(cfg, dict) else status)

    content = open(a.filter, encoding="utf-8").read()
    form = {"id": FILTER_ID, "name": "FluxPath Judgment Layer", "content": content,
            "meta": {"description": "Asks the iLang judgment endpoint before every model request.", "manifest": {}}}
    status, existing = call("GET", "/api/v1/functions/id/%s" % FILTER_ID, token=token)
    if status == 200 and existing:
        status, fn = call("POST", "/api/v1/functions/id/%s/update" % FILTER_ID, form, token)
        print("filter updated:", status)
    else:
        status, fn = call("POST", "/api/v1/functions/create", form, token)
        print("filter created:", status, "" if status == 200 else fn)
    status, fn = call("GET", "/api/v1/functions/id/%s" % FILTER_ID, token=token)
    if not fn.get("is_active"):
        status, fn = call("POST", "/api/v1/functions/id/%s/toggle" % FILTER_ID, token=token)
    if not fn.get("is_global"):
        status, fn = call("POST", "/api/v1/functions/id/%s/toggle/global" % FILTER_ID, token=token)
    print("filter type=%s active=%s global=%s" % (fn.get("type"), fn.get("is_active"), fn.get("is_global")))

    judge_token = open(a.judge_token_file, encoding="utf-8").read().strip() if a.judge_token_file else ""
    status, valves = call("POST", "/api/v1/functions/id/%s/valves/update" % FILTER_ID,
                          {"judge_url": a.judge_url, "judge_token": judge_token}, token)
    print("valves:", status, {k: v for k, v in (valves or {}).items() if k != "judge_token"},
          "token set" if judge_token else "no token")
    return 0


if __name__ == "__main__":
    sys.exit(main())

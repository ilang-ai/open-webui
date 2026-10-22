# -*- coding: utf-8 -*-
"""Cloudflare side of the FluxPath deployment, one step per subcommand, each safe to run again.
Reads the account token from ~/.claude/secrets/cloudflare-ilang-account.env and keeps ids in
deploy/cf-state.json (no secrets). The tunnel token goes to .local/secrets/tunnel.env.

  python cf_setup.py status      read-only: zone, records, tunnel, pages project
  python cf_setup.py ssl         zone SSL mode -> Full (strict)
  python cf_setup.py tunnel      create tunnel "fluxpath", write its token, set ingress app.fluxpath.app -> http://fluxpath:8080
  python cf_setup.py dns-app     CNAME app -> <tunnel>.cfargotunnel.com (proxied)
  python cf_setup.py pages       create Pages project "fluxpath" and attach fluxpath.app + www.fluxpath.app
  python cf_setup.py dns-root    CNAME @ and www -> fluxpath.pages.dev (proxied)
  (the landing files are uploaded separately: npx wrangler pages deploy landing --project-name fluxpath)
"""
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "deploy", "cf-state.json")
TUNNEL_ENV = os.path.join(ROOT, ".local", "secrets", "tunnel.env")
ZONE_NAME = "fluxpath.app"
API = "https://api.cloudflare.com/client/v4"


def secrets():
    vals = {}
    for line in open(os.path.expanduser(r"~\.claude\secrets\cloudflare-ilang-account.env"), encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip('"')
    return vals["CF_ACCOUNT_ID"], vals["CF_ACCOUNT_TOKEN"]


ACCOUNT, TOKEN = secrets()


def cf(method, path, data=None):
    req = urllib.request.Request(API + path, data=json.dumps(data).encode("utf-8") if data is not None else None,
                                 headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        out = json.loads(e.read().decode("utf-8", "replace") or "{}")
    if not out.get("success"):
        raise SystemExit("%s %s failed: %s" % (method, path, json.dumps(out.get("errors"), ensure_ascii=False)[:400]))
    return out["result"]


def state():
    return json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}


def save(st):
    json.dump(st, open(STATE, "w", encoding="utf-8"), indent=2)


def zone_id():
    st = state()
    if "zone_id" not in st:
        zones = cf("GET", "/zones?name=%s&account.id=%s" % (ZONE_NAME, ACCOUNT))
        if not zones:
            raise SystemExit("zone %s not in this account" % ZONE_NAME)
        st["zone_id"] = zones[0]["id"]
        save(st)
    return st["zone_id"]


def upsert_cname(name, content):
    zid = zone_id()
    fqdn = ZONE_NAME if name == "@" else "%s.%s" % (name, ZONE_NAME)
    existing = cf("GET", "/zones/%s/dns_records?name=%s" % (zid, fqdn))
    body = {"type": "CNAME", "name": fqdn, "content": content, "proxied": True, "ttl": 1}
    if existing:
        rec = existing[0]
        if rec["type"] == "CNAME" and rec["content"] == content and rec.get("proxied"):
            print("dns %s -> %s already set" % (fqdn, content))
            return
        cf("PUT", "/zones/%s/dns_records/%s" % (zid, rec["id"]), body)
        print("dns %s updated -> %s" % (fqdn, content))
    else:
        cf("POST", "/zones/%s/dns_records" % zid, body)
        print("dns %s created -> %s" % (fqdn, content))


def cmd_status():
    zid = zone_id()
    z = cf("GET", "/zones/%s" % zid)
    print("zone:", z["name"], z["status"], z["plan"]["name"])
    print("ssl:", cf("GET", "/zones/%s/settings/ssl" % zid)["value"])
    for r in cf("GET", "/zones/%s/dns_records" % zid):
        print("record:", r["type"], r["name"], "->", r["content"], "proxied" if r.get("proxied") else "")
    for t in cf("GET", "/accounts/%s/cfd_tunnel?is_deleted=false" % ACCOUNT):
        print("tunnel:", t["name"], t["id"], t.get("status"))
    try:
        p = cf("GET", "/accounts/%s/pages/projects/fluxpath" % ACCOUNT)
        print("pages: fluxpath", p.get("subdomain"), [d for d in p.get("domains", [])])
    except SystemExit as e:
        print("pages: none (%s)" % str(e)[:80])


def cmd_ssl():
    zid = zone_id()
    cur = cf("GET", "/zones/%s/settings/ssl" % zid)["value"]
    if cur != "strict":
        cf("PATCH", "/zones/%s/settings/ssl" % zid, {"value": "strict"})
    print("ssl:", "strict")


def cmd_tunnel():
    st = state()
    tunnels = [t for t in cf("GET", "/accounts/%s/cfd_tunnel?is_deleted=false&name=fluxpath" % ACCOUNT)]
    if tunnels:
        tid = tunnels[0]["id"]
        print("tunnel fluxpath exists:", tid)
    else:
        tid = cf("POST", "/accounts/%s/cfd_tunnel" % ACCOUNT, {"name": "fluxpath", "config_src": "cloudflare"})["id"]
        print("tunnel fluxpath created:", tid)
    st["tunnel_id"] = tid
    save(st)
    cf("PUT", "/accounts/%s/cfd_tunnel/%s/configurations" % (ACCOUNT, tid), {"config": {"ingress": [
        {"hostname": "app.%s" % ZONE_NAME, "service": "http://fluxpath:8080"},
        {"service": "http_status:404"},
    ]}})
    print("ingress: app.%s -> http://fluxpath:8080" % ZONE_NAME)
    token = cf("GET", "/accounts/%s/cfd_tunnel/%s/token" % (ACCOUNT, tid))
    os.makedirs(os.path.dirname(TUNNEL_ENV), exist_ok=True)
    with open(TUNNEL_ENV, "w", encoding="utf-8") as f:
        f.write("TUNNEL_TOKEN=%s\n" % token)
    print("tunnel token written to", TUNNEL_ENV)


def cmd_dns_app():
    tid = state().get("tunnel_id") or sys.exit("run tunnel first")
    upsert_cname("app", "%s.cfargotunnel.com" % tid)


def cmd_pages():
    try:
        cf("GET", "/accounts/%s/pages/projects/fluxpath" % ACCOUNT)
        print("pages project fluxpath exists")
    except SystemExit:
        cf("POST", "/accounts/%s/pages/projects" % ACCOUNT, {"name": "fluxpath", "production_branch": "main"})
        print("pages project fluxpath created")
    have = {d["name"] if isinstance(d, dict) else d for d in cf("GET", "/accounts/%s/pages/projects/fluxpath/domains" % ACCOUNT)}
    for d in (ZONE_NAME, "www." + ZONE_NAME):
        if d in have:
            print("pages domain exists:", d)
        else:
            cf("POST", "/accounts/%s/pages/projects/fluxpath/domains" % ACCOUNT, {"name": d})
            print("pages domain added:", d)


def cmd_dns_root():
    upsert_cname("@", "fluxpath.pages.dev")
    upsert_cname("www", "fluxpath.pages.dev")


if __name__ == "__main__":
    cmds = {"status": cmd_status, "ssl": cmd_ssl, "tunnel": cmd_tunnel, "dns-app": cmd_dns_app, "pages": cmd_pages, "dns-root": cmd_dns_root}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    cmds[sys.argv[1]]()

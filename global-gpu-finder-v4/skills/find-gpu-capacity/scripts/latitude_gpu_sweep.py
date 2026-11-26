#!/usr/bin/env python3
"""List Latitude.sh GPU plans (bare metal and GPU VMs) with per-location stock and pricing.

Fallback for when the Latitude.sh MCP server is not connected. Read only, standard
library only. Needs a Latitude.sh API key in the LATITUDESH_BEARER environment variable
(a read-only key is enough): https://www.latitude.sh/dashboard/api-keys

  export LATITUDESH_BEARER=...
  python3 latitude_gpu_sweep.py --gpu b300 rtx6000pro --format table
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request

API = "https://api.latitude.sh"

TOKENS = {
    "b300": ["b300"], "gb300": ["gb300"], "b200": ["b200"], "gb200": ["gb200"],
    "h200": ["h200"], "h100": ["h100"], "a100": ["a100"],
    "rtx6000pro": ["rtxpro6000", "rtx6000pro", "rtx6kpro", "rtxproserver6000"],
    "l40s": ["l40s"], "l4": ["l4"], "a10g": ["a10g", "a10"], "t4": ["t4"],
}
ALIASES = {"rtxpro6000": "rtx6000pro", "rtx6kpro": "rtx6000pro", "rtx6000": "rtx6000pro", "a10": "a10g"}


def norm(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def get(path, params=None):
    token = os.environ.get("LATITUDESH_BEARER")
    if not token:
        raise SystemExit("Set LATITUDESH_BEARER to a Latitude.sh API key first.")
    if not token.lower().startswith("bearer "):
        token = f"Bearer {token}"
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": token, "Accept": "application/json",
                                               "User-Agent": "global-gpu-finder/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def gpu_key(text):
    n = norm(text)
    for key in sorted(TOKENS, key=lambda k: -max(len(t) for t in TOKENS[k])):
        for tok in TOKENS[key]:
            # Short tokens (l4, t4, a10) must match a whole word, not part of another name.
            if len(tok) <= 3:
                if re.search(rf"(^|[^a-z0-9]){tok}([^a-z0-9]|$)", (text or "").lower().replace("-", " ")):
                    return key
            elif tok in n:
                return key
    return None


def site_names():
    names = {}
    try:
        page = 1
        while True:
            data = get("/regions", {"page[size]": 100, "page[number]": page})
            for d in data.get("data", []):
                a = d.get("attributes", {})
                country = (a.get("country") or {}).get("name", "")
                names[a.get("slug")] = f"{a.get('name', '')}, {country}".strip(", ")
            if len(data.get("data", [])) < 100:
                break
            page += 1
    except Exception as e:  # names are a nice to have
        names["_error"] = str(e)
    return names


def usd(pricing, currency):
    p = (pricing or {}).get(currency) or {}
    return p.get("hour"), p.get("month")


def rows_from_metal(plans, keys, currency, sites):
    rows = []
    for p in plans:
        a = p.get("attributes", {})
        g = (a.get("specs") or {}).get("gpu") or {}
        key = gpu_key(g.get("type")) or gpu_key(a.get("slug"))
        if keys and key not in keys:
            continue
        for reg in a.get("regions", []):
            locs = reg.get("locations") or {}
            hour, month = usd(reg.get("pricing"), currency)
            for site in locs.get("available", []):
                rows.append({
                    "provider": "latitude", "product": "Metal GPU", "plan": a.get("slug"),
                    "gpu_key": key, "gpu": g.get("type"), "gpu_count": g.get("count"),
                    "vram_per_gpu_gb": g.get("vram_per_gpu"), "interconnect": g.get("interconnect"),
                    "region": reg.get("name"), "site": site, "site_name": sites.get(site, ""),
                    "in_stock": site in (locs.get("in_stock") or []),
                    "deploys_instantly": site in (reg.get("deploys_instantly") or []),
                    "stock_level": reg.get("stock_level"),
                    "price_hour": hour, "price_month": month, "currency": currency,
                })
    return rows


def rows_from_vms(plans, keys, currency, sites):
    rows = []
    for p in plans:
        a = p.get("attributes", {})
        s = a.get("specs") or {}
        key = gpu_key(s.get("gpu")) or gpu_key(a.get("slug"))
        if not s.get("gpu") or (keys and key not in keys):
            continue
        for reg in a.get("regions", []):
            locs = reg.get("locations") or {}
            hour, month = usd(reg.get("pricing"), currency)
            for site in locs.get("available", []) or reg.get("available", []):
                rows.append({
                    "provider": "latitude", "product": "GPU VM", "plan": a.get("slug"),
                    "gpu_key": key, "gpu": s.get("gpu"), "gpu_count": None,
                    "vram_per_gpu_gb": s.get("vram_per_gpu"), "interconnect": None,
                    "region": reg.get("name"), "site": site, "site_name": sites.get(site, ""),
                    "in_stock": site in (locs.get("in_stock") or []),
                    "deploys_instantly": None, "stock_level": reg.get("stock_level"),
                    "price_hour": hour, "price_month": month, "currency": currency,
                })
    return rows


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gpu", nargs="+", default=["all"], help="GPU keys, e.g. b300 rtx6000pro, or all")
    ap.add_argument("--currency", default="USD")
    ap.add_argument("--in-stock-only", action="store_true")
    ap.add_argument("--no-vms", action="store_true", help="skip GPU VM plans")
    ap.add_argument("--format", choices=["json", "table"], default="json")
    a = ap.parse_args(argv)

    keys = [ALIASES.get(norm(k), norm(k)) for k in a.gpu]
    keys = [] if "all" in keys else keys

    sites = site_names()
    rows = rows_from_metal(get("/plans", {"filter[gpu]": "true"}).get("data", []), keys, a.currency, sites)
    if not a.no_vms:
        try:
            vm = get("/plans/virtual_machines", {"filter[gpu]": "true"}).get("data", [])
            rows += rows_from_vms(vm, keys, a.currency, sites)
        except Exception as e:
            print(f"warning: VM plans not listed: {e}", file=sys.stderr)
    if a.in_stock_only:
        rows = [r for r in rows if r["in_stock"]]

    if a.format == "json":
        print(json.dumps({"provider": "latitude", "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                          "rows": rows}, indent=2))
        return
    header = ["plan", "product", "gpus", "site", "location", "stock", "in_stock", "price_hr", "per_gpu_hr"]
    out = []
    for r in rows:
        n = r["gpu_count"] or 1
        ph = r["price_hour"]
        out.append([r["plan"], r["product"], f"{r['gpu_count'] or ''}x {r['gpu'] or ''}".strip("x "),
                    r["site"], r["site_name"], r["stock_level"] or "", "yes" if r["in_stock"] else "no",
                    f"{ph:.2f}" if ph else "", f"{ph / n:.2f}" if ph else ""])
    widths = [max(len(str(x)) for x in col) for col in zip(header, *out)] if out else [len(h) for h in header]
    print("  ".join(h.ljust(w) for h, w in zip(header, widths)))
    for row in out:
        print("  ".join(str(c).ljust(w) for c, w in zip(row, widths)))
    print(f"\nPrices in {a.currency}. Live stock changes quickly, so confirm in the dashboard before committing.")


if __name__ == "__main__":
    main(sys.argv[1:])

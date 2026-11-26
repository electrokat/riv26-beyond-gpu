#!/usr/bin/env python3
"""Sweep every enabled AWS region for NVIDIA GPU instance types.

Read only. Uses these EC2 / Pricing / Service Quotas calls:
  ec2:DescribeRegions, ec2:DescribeInstanceTypes, ec2:DescribeInstanceTypeOfferings,
  ec2:DescribeCapacityBlockOfferings, ec2:GetSpotPlacementScores,
  pricing:GetProducts, servicequotas:ListServiceQuotas

Two ways to run it:
  1. Locally:  python3 aws_gpu_sweep.py --gpu b300 rtx6000pro --pricing --capacity-blocks
  2. Inside the AWS MCP Server's aws___run_script sandbox: pass the whole file and
     edit DEFAULTS below (the sandbox passes no command line arguments).

Output is JSON on stdout (or a plain table with --format table).
"""

import argparse
import datetime as dt
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

# Edit these when running inside aws___run_script.
DEFAULTS = {
    "gpu": ["b300", "rtx6000pro"],   # GPU keys from references/sku-map.md, or ["all"]
    "regions": [],                    # empty = every region enabled on the account
    "count": 1,                       # instances wanted (used for Capacity Blocks and spot scores)
    "hours": 24,                      # Capacity Block duration to look for
    "days_ahead": 14,                 # how far ahead to look for Capacity Block start dates
    "pricing": True,                  # look up Linux On-Demand price per hour
    "capacity_blocks": True,          # look for reservable Capacity Blocks
    "spot_scores": True,              # spot placement score 1-10 per region
    "quotas": False,                  # On-Demand vCPU quotas for P and G families
}

# Match tokens are compared with GPU names lower cased with non alphanumerics removed.
GPU_KEYS = {
    "b300": {"tokens": ["b300"], "exact": False, "static": ["p6-b300.48xlarge"]},
    "gb300": {"tokens": ["gb300"], "exact": False, "static": []},
    "b200": {"tokens": ["b200"], "exact": False, "static": ["p6-b200.48xlarge"]},
    "gb200": {"tokens": ["gb200"], "exact": False, "static": ["p6e-gb200.36xlarge"]},
    "h200": {"tokens": ["h200"], "exact": False, "static": ["p5en.48xlarge", "p5e.48xlarge"]},
    "h100": {"tokens": ["h100"], "exact": False, "static": ["p5.48xlarge", "p5.4xlarge"]},
    "a100": {"tokens": ["a100"], "exact": False, "static": ["p4de.24xlarge", "p4d.24xlarge"]},
    "rtx6000pro": {
        "tokens": ["rtxpro6000", "rtx6000pro", "rtx6kpro", "rtxproserver6000"],
        "exact": False,
        "static": ["g7e.2xlarge", "g7e.4xlarge", "g7e.8xlarge",
                   "g7e.12xlarge", "g7e.24xlarge", "g7e.48xlarge"],
    },
    "l40s": {"tokens": ["l40s"], "exact": False, "static": []},
    "l4": {"tokens": ["l4"], "exact": True, "static": []},
    "a10g": {"tokens": ["a10g", "a10"], "exact": True, "static": []},
    "t4": {"tokens": ["t4"], "exact": True, "static": []},
}

ALIASES = {
    "rtxpro6000": "rtx6000pro", "rtx6kpro": "rtx6000pro", "rtx6000": "rtx6000pro",
    "rtxpro": "rtx6000pro", "a10": "a10g",
}

CFG = Config(retries={"max_attempts": 8, "mode": "adaptive"}, connect_timeout=10, read_timeout=30)
PRICING_LOCATION_REGION = "us-east-1"
CB_FAMILY_PREFIXES = ("p4d.", "p4de.", "p5.", "p5e.", "p5en.", "p6-", "p6e-", "trn1.", "trn2.")


def norm(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def resolve_keys(requested):
    keys = []
    for raw in requested:
        k = ALIASES.get(norm(raw), norm(raw))
        if k == "all":
            return list(GPU_KEYS)
        if k not in GPU_KEYS:
            raise SystemExit(f"Unknown GPU '{raw}'. Known keys: {', '.join(GPU_KEYS)}")
        if k not in keys:
            keys.append(k)
    return keys


def gpu_key_for_name(gpu_name):
    n = norm(gpu_name)
    # Check longer, more specific keys first so gb200 wins over b200 and l40s over l4.
    for key in sorted(GPU_KEYS, key=lambda x: -max(len(t) for t in GPU_KEYS[x]["tokens"])):
        spec = GPU_KEYS[key]
        for tok in spec["tokens"]:
            if (spec["exact"] and n == tok) or (not spec["exact"] and tok in n):
                return key
    return None


def discover_gpu_types(session, seed_regions):
    """Return {instance_type: spec} for every NVIDIA GPU instance type seen in the seed regions."""
    found = {}
    for region in seed_regions:
        ec2 = session.client("ec2", region_name=region, config=CFG)
        try:
            for page in ec2.get_paginator("describe_instance_types").paginate():
                for it in page["InstanceTypes"]:
                    gpus = (it.get("GpuInfo") or {}).get("Gpus") or []
                    if not gpus or it["InstanceType"] in found:
                        continue
                    g = gpus[0]
                    found[it["InstanceType"]] = {
                        "gpu_name": g.get("Name"),
                        "gpu_manufacturer": g.get("Manufacturer"),
                        "gpu_count": sum(x.get("Count", 0) for x in gpus),
                        "gpu_mem_gib_each": round((g.get("MemoryInfo") or {}).get("SizeInMiB", 0) / 1024, 1),
                        "gpu_mem_gib_total": round((it["GpuInfo"].get("TotalGpuMemoryInMiB") or 0) / 1024, 1),
                        "vcpus": it.get("VCpuInfo", {}).get("DefaultVCpus"),
                        "mem_gib": round(it.get("MemoryInfo", {}).get("SizeInMiB", 0) / 1024, 1),
                        "network": it.get("NetworkInfo", {}).get("NetworkPerformance"),
                        "gpu_key": gpu_key_for_name(g.get("Name")),
                    }
        except (ClientError, BotoCoreError) as e:
            found.setdefault("_errors", []).append(f"describe_instance_types {region}: {e}")
    return found


def candidate_types(keys, discovered):
    cands = {}
    for it, spec in discovered.items():
        if it.startswith("_"):
            continue
        if spec.get("gpu_key") in keys:
            cands[it] = spec
    for k in keys:
        for it in GPU_KEYS[k]["static"]:
            cands.setdefault(it, {"gpu_key": k, "gpu_name": k.upper(), "note": "from static map, not seen in seed regions"})
    return cands


def offerings_for_region(session, region, types):
    ec2 = session.client("ec2", region_name=region, config=CFG)
    out = {}
    type_list = sorted(types)
    for i in range(0, len(type_list), 100):
        chunk = type_list[i:i + 100]
        pages = ec2.get_paginator("describe_instance_type_offerings").paginate(
            LocationType="availability-zone",
            Filters=[{"Name": "instance-type", "Values": chunk}],
        )
        for page in pages:
            for o in page["InstanceTypeOfferings"]:
                out.setdefault(o["InstanceType"], []).append(o["Location"])
    return {k: sorted(v) for k, v in out.items()}


def capacity_blocks(session, region, itype, count, hours, days_ahead):
    ec2 = session.client("ec2", region_name=region, config=CFG)
    now = dt.datetime.now(dt.timezone.utc)
    resp = ec2.describe_capacity_block_offerings(
        InstanceType=itype,
        InstanceCount=count,
        CapacityDurationHours=hours,
        StartDateRange=now,
        EndDateRange=now + dt.timedelta(days=days_ahead),
    )
    offers = resp.get("CapacityBlockOfferings", [])
    if not offers:
        return {"offers": 0}
    earliest = min(offers, key=lambda o: o["StartDate"])
    cheapest = min(offers, key=lambda o: float(o.get("UpfrontFee", "inf")))
    fee = float(cheapest.get("UpfrontFee", 0))
    return {
        "offers": len(offers),
        "earliest_start": earliest["StartDate"].isoformat(),
        "earliest_az": earliest.get("AvailabilityZone"),
        "cheapest_upfront_fee": fee,
        "currency": cheapest.get("CurrencyCode", "USD"),
        "cheapest_per_instance_hour": round(fee / max(hours * count, 1), 2),
    }


def on_demand_price(session, region, itype):
    pricing = session.client("pricing", region_name=PRICING_LOCATION_REGION, config=CFG)
    filters = [
        {"Type": "TERM_MATCH", "Field": "instanceType", "Value": itype},
        {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
        {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
        {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
        {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
        {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
    ]
    resp = pricing.get_products(ServiceCode="AmazonEC2", Filters=filters, MaxResults=10)
    for raw in resp.get("PriceList", []):
        item = json.loads(raw)
        for term in item.get("terms", {}).get("OnDemand", {}).values():
            for dim in term.get("priceDimensions", {}).values():
                usd = dim.get("pricePerUnit", {}).get("USD")
                if usd and float(usd) > 0:
                    return float(usd)
    return None


def spot_scores(session, itype, count, regions):
    ec2 = session.client("ec2", region_name="us-east-1", config=CFG)
    resp = ec2.get_spot_placement_scores(
        InstanceTypes=[itype],
        TargetCapacity=count,
        TargetCapacityUnitType="units",
        SingleAvailabilityZone=False,
        RegionNames=regions,
    )
    return {s["Region"]: s["Score"] for s in resp.get("SpotPlacementScores", [])}


def quotas_for_region(session, region):
    sq = session.client("service-quotas", region_name=region, config=CFG)
    wanted = {"Running On-Demand P instances": "p_vcpus", "Running On-Demand G and VT instances": "g_vcpus"}
    out = {}
    for page in sq.get_paginator("list_service_quotas").paginate(ServiceCode="ec2"):
        for q in page["Quotas"]:
            if q["QuotaName"] in wanted:
                out[wanted[q["QuotaName"]]] = q["Value"]
    return out


def sweep(opts):
    session = boto3.Session()
    keys = resolve_keys(opts["gpu"])
    errors = []

    if opts["regions"]:
        regions = opts["regions"]
    else:
        ec2 = session.client("ec2", region_name="us-east-1", config=CFG)
        regions = sorted(r["RegionName"] for r in ec2.describe_regions(AllRegions=False)["Regions"])

    discovered = discover_gpu_types(session, ["us-east-1", "us-west-2"])
    errors += discovered.pop("_errors", [])
    types = candidate_types(keys, discovered)

    def do_region(region):
        rec = {"region": region, "offered": {}, "pricing_usd_hour": {}, "capacity_blocks": {}, "quotas": {}}
        try:
            rec["offered"] = offerings_for_region(session, region, types.keys())
        except (ClientError, BotoCoreError) as e:
            rec["error"] = f"offerings: {e}"
            return rec
        for itype in rec["offered"]:
            if opts["pricing"]:
                try:
                    rec["pricing_usd_hour"][itype] = on_demand_price(session, region, itype)
                except (ClientError, BotoCoreError) as e:
                    rec.setdefault("warnings", []).append(f"pricing {itype}: {e}")
            if opts["capacity_blocks"] and itype.startswith(CB_FAMILY_PREFIXES):
                try:
                    rec["capacity_blocks"][itype] = capacity_blocks(
                        session, region, itype, opts["count"], opts["hours"], opts["days_ahead"])
                except (ClientError, BotoCoreError) as e:
                    rec["capacity_blocks"][itype] = {"error": str(e)[:200]}
        if opts["quotas"]:
            try:
                rec["quotas"] = quotas_for_region(session, region)
            except (ClientError, BotoCoreError) as e:
                rec.setdefault("warnings", []).append(f"quotas: {e}")
        return rec

    with ThreadPoolExecutor(max_workers=12) as pool:
        region_results = list(pool.map(do_region, regions))

    scores = {}
    if opts["spot_scores"]:
        for itype in types:
            offered_in = [r["region"] for r in region_results if itype in r["offered"]]
            if not offered_in:
                continue
            try:
                scores[itype] = spot_scores(session, itype, opts["count"], offered_in)
            except (ClientError, BotoCoreError) as e:
                scores[itype] = {"error": str(e)[:200]}

    return {
        "provider": "aws",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "gpu_keys": keys,
        "instance_types": types,
        "regions": [r for r in region_results if r["offered"] or r.get("error")],
        "regions_checked": len(regions),
        "spot_placement_scores": scores,
        "errors": errors,
        "caveat": "Offered means AWS sells the type in that region or AZ. It does not guarantee "
                  "On-Demand capacity right now. Capacity Block offers are real reservable capacity.",
    }


def print_table(result):
    types = result["instance_types"]
    rows = []
    for r in result["regions"]:
        for itype, azs in sorted(r["offered"].items()):
            spec = types.get(itype, {})
            price = r["pricing_usd_hour"].get(itype)
            gcount = spec.get("gpu_count") or 0
            per_gpu = round(price / gcount, 2) if price and gcount else None
            cb = r["capacity_blocks"].get(itype, {})
            score = result["spot_placement_scores"].get(itype, {}).get(r["region"], "")
            rows.append([
                r["region"], itype, f"{gcount}x {spec.get('gpu_name', '?')}",
                len(azs), f"{price:.2f}" if price else "", f"{per_gpu:.2f}" if per_gpu else "",
                cb.get("earliest_start", "")[:16] if cb.get("offers") else ("none" if cb else ""),
                score,
            ])
    header = ["region", "instance", "gpus", "azs", "od_usd_hr", "usd_gpu_hr", "cap_block_start", "spot_score"]
    widths = [max(len(str(x)) for x in col) for col in zip(header, *rows)] if rows else [len(h) for h in header]
    line = "  ".join(h.ljust(w) for h, w in zip(header, widths))
    print(line)
    print("-" * len(line))
    for row in rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(row, widths)))
    print(f"\n{result['regions_checked']} regions checked. {result['caveat']}")


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--gpu", nargs="+", default=DEFAULTS["gpu"], help="GPU keys, e.g. b300 rtx6000pro h200, or all")
    p.add_argument("--regions", nargs="*", default=DEFAULTS["regions"])
    p.add_argument("--count", type=int, default=DEFAULTS["count"])
    p.add_argument("--hours", type=int, default=DEFAULTS["hours"])
    p.add_argument("--days-ahead", type=int, default=DEFAULTS["days_ahead"])
    p.add_argument("--pricing", action=argparse.BooleanOptionalAction, default=DEFAULTS["pricing"])
    p.add_argument("--capacity-blocks", action=argparse.BooleanOptionalAction, default=DEFAULTS["capacity_blocks"])
    p.add_argument("--spot-scores", action=argparse.BooleanOptionalAction, default=DEFAULTS["spot_scores"])
    p.add_argument("--quotas", action=argparse.BooleanOptionalAction, default=DEFAULTS["quotas"])
    p.add_argument("--format", choices=["json", "table"], default="json")
    return p.parse_args(argv)


if __name__ == "__main__":
    a = parse_args(sys.argv[1:])
    opts = {
        "gpu": a.gpu, "regions": a.regions, "count": a.count, "hours": a.hours,
        "days_ahead": a.days_ahead, "pricing": a.pricing, "capacity_blocks": a.capacity_blocks,
        "spot_scores": a.spot_scores, "quotas": a.quotas,
    }
    res = sweep(opts)
    if a.format == "table":
        print_table(res)
    else:
        print(json.dumps(res, indent=2, default=str))

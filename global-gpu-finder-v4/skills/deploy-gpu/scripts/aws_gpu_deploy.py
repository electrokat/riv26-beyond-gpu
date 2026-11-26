#!/usr/bin/env python3
"""Plan and launch a GPU instance on Amazon EC2, or buy an EC2 Capacity Block.

Safe by default: every action is a plan or an EC2 DryRun until you pass --confirm
(or set "confirm": True in DEFAULTS when running inside aws___run_script).

Actions:
  plan            Resolve AMI, subnet, security group, key pair and quota. Changes nothing.
  launch          On-Demand launch. DryRun unless --confirm.
  cb-offers       List Capacity Block offers for the instance type in the AZ/region.
  cb-buy          Buy one Capacity Block offer (upfront, non-refundable). Needs --offer-id and --confirm.
  cb-launch       Launch into an active Capacity Block. Needs --reservation-id. DryRun unless --confirm.
  status          Show state and IPs of instances created by this power (tag created-by=global-gpu-finder).

Examples:
  python3 aws_gpu_deploy.py plan --region us-east-1 --instance-type g7e.2xlarge --key-name my-key --ssh-cidr 203.0.113.10/32
  python3 aws_gpu_deploy.py launch --region us-east-1 --az us-east-1b --instance-type g7e.2xlarge --key-name my-key --ssh-cidr 203.0.113.10/32 --confirm
"""

import argparse
import datetime as dt
import json
import sys

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

DEFAULTS = {
    "action": "plan",                 # plan | launch | cb-offers | cb-buy | cb-launch | status
    "region": "us-east-1",
    "az": None,                       # e.g. "us-east-1b"; None lets the script pick an AZ that offers the type
    "instance_type": "g7e.2xlarge",
    "count": 1,
    "key_name": None,                 # existing EC2 key pair name
    "public_key": None,               # OpenSSH public key text; imported as key_name if the pair does not exist
    "ssh_cidr": None,                 # e.g. "203.0.113.10/32"; required to open SSH
    "ami": None,                      # override; default is the latest Deep Learning Base OSS Nvidia GPU AMI
    "subnet_id": None,
    "root_gb": 200,
    "name": "gpu-finder",
    "hours": 24,                      # Capacity Block duration
    "days_ahead": 14,
    "offer_id": None,
    "reservation_id": None,
    "confirm": False,                 # must be True to change anything
}

TAG_KEY, TAG_VAL = "created-by", "global-gpu-finder"
SG_NAME = "gpu-finder-ssh"
DLAMI_PARAM = {
    "x86_64": "/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-24.04/latest/ami-id",
}
QUOTA_NAMES = {"p": "Running On-Demand P instances", "g": "Running On-Demand G and VT instances"}
CFG = Config(retries={"max_attempts": 6, "mode": "adaptive"})


class Ctx:
    def __init__(self, o):
        self.o = o
        s = boto3.Session()
        self.ec2 = s.client("ec2", region_name=o["region"], config=CFG)
        self.ssm = s.client("ssm", region_name=o["region"], config=CFG)
        self.sq = s.client("service-quotas", region_name=o["region"], config=CFG)


def tags(o, rtype):
    return [{"ResourceType": rtype, "Tags": [{"Key": TAG_KEY, "Value": TAG_VAL}, {"Key": "Name", "Value": o["name"]}]}]


def instance_info(c):
    it = c.ec2.describe_instance_types(InstanceTypes=[c.o["instance_type"]])["InstanceTypes"][0]
    gpus = (it.get("GpuInfo") or {}).get("Gpus") or [{}]
    return {
        "vcpus": it["VCpuInfo"]["DefaultVCpus"],
        "arch": it["ProcessorInfo"]["SupportedArchitectures"][0],
        "gpu": f"{sum(g.get('Count', 0) for g in gpus)}x {gpus[0].get('Name', '?')}",
    }


def pick_az(c):
    if c.o["az"]:
        return c.o["az"]
    resp = c.ec2.describe_instance_type_offerings(
        LocationType="availability-zone",
        Filters=[{"Name": "instance-type", "Values": [c.o["instance_type"]]}])
    azs = sorted(x["Location"] for x in resp["InstanceTypeOfferings"])
    if not azs:
        raise SystemExit(f"{c.o['instance_type']} is not offered in {c.o['region']}.")
    return azs[0]


def resolve_ami(c, arch):
    if c.o["ami"]:
        return c.o["ami"], "user supplied"
    if arch not in DLAMI_PARAM:
        raise SystemExit(f"No default AMI for {arch}. Pass an AMI ID for this architecture.")
    val = c.ssm.get_parameter(Name=DLAMI_PARAM[arch])["Parameter"]["Value"]
    return val, "Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 24.04), latest"


def resolve_subnet(c, az):
    if c.o["subnet_id"]:
        sn = c.ec2.describe_subnets(SubnetIds=[c.o["subnet_id"]])["Subnets"][0]
        return sn["SubnetId"], sn["VpcId"]
    vpcs = c.ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        raise SystemExit("No default VPC in this region. Pass a subnet ID in the target AZ.")
    sns = c.ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpcs[0]["VpcId"]]},
                                          {"Name": "availability-zone", "Values": [az]}])["Subnets"]
    if not sns:
        raise SystemExit(f"Default VPC has no subnet in {az}. Pass a subnet ID.")
    return sns[0]["SubnetId"], vpcs[0]["VpcId"]


def find_sg(c, vpc_id):
    sgs = c.ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpc_id]},
                                                  {"Name": "group-name", "Values": [SG_NAME]}])["SecurityGroups"]
    return sgs[0]["GroupId"] if sgs else None


def ensure_sg(c, vpc_id):
    sg = find_sg(c, vpc_id)
    if sg:
        return sg
    if not c.o["ssh_cidr"]:
        raise SystemExit("Pass the user's public IP as ssh_cidr (for example 203.0.113.10/32) to open SSH.")
    sg = c.ec2.create_security_group(GroupName=SG_NAME, Description="SSH for GPU instances from Global GPU Finder",
                                     VpcId=vpc_id, TagSpecifications=tags(c.o, "security-group"))["GroupId"]
    c.ec2.authorize_security_group_ingress(GroupId=sg, IpPermissions=[{
        "IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
        "IpRanges": [{"CidrIp": c.o["ssh_cidr"], "Description": "SSH from user"}]}])
    return sg


def key_exists(c):
    if not c.o["key_name"]:
        return False
    try:
        c.ec2.describe_key_pairs(KeyNames=[c.o["key_name"]])
        return True
    except ClientError as e:
        if "NotFound" in e.response["Error"]["Code"]:
            return False
        raise


def ensure_key(c):
    if key_exists(c):
        return c.o["key_name"]
    if not (c.o["key_name"] and c.o["public_key"]):
        raise SystemExit("Pass an existing key pair name, or a key name plus the user's OpenSSH public key to import.")
    c.ec2.import_key_pair(KeyName=c.o["key_name"], PublicKeyMaterial=c.o["public_key"].encode(),
                          TagSpecifications=tags(c.o, "key-pair"))
    return c.o["key_name"]


def quota(c, family_letter):
    name = QUOTA_NAMES.get(family_letter)
    if not name:
        return None
    for page in c.sq.get_paginator("list_service_quotas").paginate(ServiceCode="ec2"):
        for q in page["Quotas"]:
            if q["QuotaName"] == name:
                return q["Value"]
    return None


def plan(c):
    info = instance_info(c)
    az = pick_az(c)
    ami, ami_desc = resolve_ami(c, info["arch"])
    subnet, vpc = resolve_subnet(c, az)
    fam = c.o["instance_type"][0]
    q = quota(c, fam)
    need = info["vcpus"] * c.o["count"]
    return {
        "region": c.o["region"], "az": az, "instance_type": c.o["instance_type"], "count": c.o["count"],
        "gpu": info["gpu"], "vcpus_needed": need, "quota_vcpus": q,
        "quota_ok": (q is None) or (q >= need),
        "ami": ami, "ami_description": ami_desc, "subnet_id": subnet, "vpc_id": vpc,
        "security_group": find_sg(c, vpc) or f"will create {SG_NAME} allowing SSH from {c.o['ssh_cidr'] or '(ssh_cidr needed)'}",
        "key_pair": c.o["key_name"] if key_exists(c) else f"will import {c.o['key_name'] or '(key name needed)'}",
        "root_volume_gb": c.o["root_gb"], "tags": {TAG_KEY: TAG_VAL, "Name": c.o["name"]},
        "notes": [
            "Quota counts all running instances in the family. If current usage plus this launch exceeds it, the launch fails.",
            "Single instance networking only. Multi-node training on P5/P6 needs EFA and a placement group, which this script does not set up.",
        ],
    }


def run_params(c, p, sg, extra=None):
    params = {
        "ImageId": p["ami"], "InstanceType": p["instance_type"], "MinCount": p["count"], "MaxCount": p["count"],
        "KeyName": c.o["key_name"],
        "NetworkInterfaces": [{"DeviceIndex": 0, "SubnetId": p["subnet_id"], "AssociatePublicIpAddress": True,
                               **({"Groups": [sg]} if sg else {})}],
        "BlockDeviceMappings": [{"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": p["root_volume_gb"],
                                                                    "VolumeType": "gp3", "DeleteOnTermination": True}}],
        "MetadataOptions": {"HttpTokens": "required"},
        "TagSpecifications": tags(c.o, "instance") + tags(c.o, "volume"),
    }
    params.update(extra or {})
    return params


def dry_run(c, params):
    try:
        c.ec2.run_instances(DryRun=True, **params)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "DryRunOperation":
            return {"dry_run": "passed", "detail": "The request would succeed (permissions and parameters are valid)."}
        return {"dry_run": "failed", "code": code, "detail": e.response["Error"]["Message"]}
    return {"dry_run": "unexpected", "detail": "DryRun returned without an error."}


def launch(c, extra=None):
    p = plan(c)
    if not p["quota_ok"]:
        return {"plan": p, "result": "blocked", "detail": "vCPU quota is below what this launch needs. Request a quota increase first."}
    if not c.o["confirm"]:
        sg = find_sg(c, p["vpc_id"])
        return {"plan": p, **dry_run(c, run_params(c, p, sg, extra)),
                "next": "Show this plan and price to the user. Re-run with confirm after they approve."}
    ensure_key(c)
    sg = ensure_sg(c, p["vpc_id"])
    resp = c.ec2.run_instances(**run_params(c, p, sg, extra))
    ids = [i["InstanceId"] for i in resp["Instances"]]
    return {"plan": p, "result": "launched", "instance_ids": ids,
            "ssh": "ssh -i <private key> ubuntu@<public IP once running>",
            "stop_billing": f"aws ec2 terminate-instances --region {c.o['region']} --instance-ids {' '.join(ids)}"}


def cb_offers(c):
    now = dt.datetime.now(dt.timezone.utc)
    kw = dict(InstanceType=c.o["instance_type"], InstanceCount=c.o["count"], CapacityDurationHours=c.o["hours"],
              StartDateRange=now, EndDateRange=now + dt.timedelta(days=c.o["days_ahead"]))
    offers = c.ec2.describe_capacity_block_offerings(**kw).get("CapacityBlockOfferings", [])
    if c.o["az"]:
        offers = [o for o in offers if o.get("AvailabilityZone") == c.o["az"]]
    return {"offers": [{
        "offer_id": o["CapacityBlockOfferingId"], "az": o.get("AvailabilityZone"),
        "start": str(o["StartDate"]), "end": str(o["EndDate"]), "instances": o.get("InstanceCount"),
        "upfront_fee": o.get("UpfrontFee"), "currency": o.get("CurrencyCode"),
    } for o in sorted(offers, key=lambda x: x["StartDate"])]}


def cb_buy(c):
    if not c.o["offer_id"]:
        raise SystemExit("Pass the offer ID to buy (from cb-offers).")
    if not c.o["confirm"]:
        return {"result": "not purchased", "detail": "Capacity Blocks are paid upfront and cannot be cancelled. "
                "Show the offer (dates, AZ, fee) to the user and re-run with confirm after they approve."}
    r = c.ec2.purchase_capacity_block(CapacityBlockOfferingId=c.o["offer_id"], InstancePlatform="Linux/UNIX",
                                      TagSpecifications=tags(c.o, "capacity-reservation"))
    cr = r["CapacityReservation"]
    return {"result": "purchased", "reservation_id": cr["CapacityReservationId"], "state": cr.get("State"),
            "az": cr.get("AvailabilityZone"), "start": str(cr.get("StartDate")), "end": str(cr.get("EndDate")),
            "next": "When the reservation state is active, run cb-launch with this reservation ID."}


def cb_launch(c):
    rid = c.o["reservation_id"]
    if not rid:
        raise SystemExit("Pass the Capacity Block reservation ID.")
    cr = c.ec2.describe_capacity_reservations(CapacityReservationIds=[rid])["CapacityReservations"][0]
    if cr["State"] != "active":
        return {"result": "waiting", "state": cr["State"], "start": str(cr.get("StartDate")),
                "detail": "The Capacity Block is not active yet. Launch after the start time."}
    c.o["az"] = cr["AvailabilityZone"]
    c.o["instance_type"] = cr["InstanceType"]
    return launch(c, extra={
        "InstanceMarketOptions": {"MarketType": "capacity-block"},
        "CapacityReservationSpecification": {"CapacityReservationTarget": {"CapacityReservationId": rid}},
    })


def status(c):
    res = c.ec2.describe_instances(Filters=[{"Name": f"tag:{TAG_KEY}", "Values": [TAG_VAL]},
                                            {"Name": "instance-state-name",
                                             "Values": ["pending", "running", "stopping", "stopped"]}])
    out = []
    for r in res["Reservations"]:
        for i in r["Instances"]:
            out.append({"instance_id": i["InstanceId"], "type": i["InstanceType"], "state": i["State"]["Name"],
                        "az": i["Placement"]["AvailabilityZone"], "public_ip": i.get("PublicIpAddress"),
                        "launched": str(i.get("LaunchTime"))})
    return {"region": c.o["region"], "instances": out}


ACTIONS = {"plan": plan, "launch": launch, "cb-offers": cb_offers, "cb-buy": cb_buy, "cb-launch": cb_launch, "status": status}


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", nargs="?", default=DEFAULTS["action"], choices=list(ACTIONS))
    for k, v in DEFAULTS.items():
        if k in ("action", "confirm"):
            continue
        ap.add_argument("--" + k.replace("_", "-"), type=type(v) if isinstance(v, int) else str, default=v)
    ap.add_argument("--confirm", action="store_true", default=DEFAULTS["confirm"])
    a = vars(ap.parse_args(argv))
    o = {k.replace("-", "_"): v for k, v in a.items()}
    print(json.dumps(ACTIONS[o["action"]](Ctx(o)), indent=2, default=str))


if __name__ == "__main__":
    main(sys.argv[1:])

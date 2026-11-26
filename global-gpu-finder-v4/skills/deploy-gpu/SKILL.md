---
name: "deploy-gpu"
description: "Deploy a GPU server on Latitude.sh (Metal GPU or GPU VM) or launch a GPU instance on Amazon EC2 (On-Demand or Capacity Block), with a plan, price check and explicit user confirmation before anything is created. Use when the user asks to deploy, launch, spin up, provision, reserve or book a GPU option, usually one found with find-gpu-capacity."
compatibility: "Kiro. Uses the Latitude.sh MCP server and the AWS MCP Server. The AWS profile needs the deploy policy in references/aws-deploy-policy.md. Local fallback needs Python 3.9+ and boto3."
metadata:
  author: "Latitude.sh"
  version: "1.1.0"
---

# Deploy GPU capacity on Latitude.sh or AWS

## Overview

This skill turns a chosen GPU option into a running server. It spends the user's money, so it follows one rule above all others:

Nothing is created, purchased or changed until the user has seen a deployment plan with the price and has replied with a clear yes to that exact plan.

Guardrails that always apply:

1. Only start this skill when the user asks to deploy, launch, provision or reserve. Finding capacity is not consent to deploy.
1a. Deploy only on the provider the user chose and signed in to in this conversation. For AWS, the account ID was confirmed with the user at sign-in; show it again in the plan. If the user switches provider, go back to sign-in for that provider first.
2. Re-check live stock and price immediately before showing the plan. Capacity moves quickly.
3. Show the plan (template below) and wait. Accept only an unambiguous approval of that plan ("yes, deploy it", "confirm"). If anything in the plan changes, show it again.
4. Default to one server. If the user asks for more than 2 servers, or the estimated cost is above USD 100 per hour, or it is an AWS Capacity Block (upfront, non-refundable), restate the total and ask for a second confirmation.
5. Tag or name everything so it can be found later (`created-by=global-gpu-finder` on AWS, hostname prefix `gpu-finder-` on Latitude.sh unless the user picks a name).
6. Never delete, terminate, reinstall or modify existing resources unless the user explicitly asks, names the resource, and confirms. Only act on resources this power created unless told otherwise.
7. Never ask for or store passwords or private keys. Use the user's SSH public key only.
8. If a step fails, stop and report the error in plain language. Do not retry with different, more expensive options without asking.

## Step 1. Collect what is needed

| Item | Latitude.sh | AWS |
|---|---|---|
| Option | plan slug and site (for example g4.rtx6kpro.large at a site code) | instance type, region and optionally AZ |
| Count | default 1 | default 1 |
| Operating system | default `ubuntu24_ml_in_a_box` if listed for the plan, otherwise `ubuntu_24_04_x64_lts` | default Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 24.04), latest |
| SSH access | an SSH key already in the Latitude.sh project, or the user's public key to add | an existing EC2 key pair, or the user's public key to import, plus the user's public IP for the SSH rule |
| Billing | hourly by default (monthly or yearly only if the user asks) | On-Demand by default, or a Capacity Block if the user wants guaranteed dates |
| Where | Latitude.sh project (pick an existing one, or create one only after asking) | default VPC, or a subnet ID the user gives |

To find the user's public key, ask them to paste it or offer to read `~/.ssh/id_ed25519.pub` or `~/.ssh/id_rsa.pub` from their machine (never the private key). For the SSH rule on AWS, ask for their public IP or have them run `curl -s https://checkip.amazonaws.com`.

## Step 2. Build and show the deployment plan

Use this template, filled with live values:

```
Deployment plan
Provider:        Latitude.sh | AWS
Account:         <Latitude.sh team/project> | <AWS account ID and profile>
Product:         Metal GPU | GPU VM | EC2 On-Demand | EC2 Capacity Block
SKU:             g4.b300.large (8x NVIDIA HGX B300)
Location:        <city, country> (<site code or region / AZ>)
Stock check:     in stock at <time> | offered, spot score 7/10 | Capacity Block <start> to <end>
Quantity:        1
OS / image:      ubuntu24_ml_in_a_box | ami-... (Deep Learning Base GPU, Ubuntu 24.04)
Access:          SSH key <name>; SSH open to <ip>/32 only (AWS)
Billing:         hourly | On-Demand | upfront <fee>, non-refundable
Estimated cost:  <price per hour> (about <price x 730> per month if left running)
Name / tags:     gpu-finder-<x> | created-by=global-gpu-finder
To stop billing: delete the server in Latitude.sh | terminate the instance | Capacity Blocks bill in full regardless

Reply "confirm" to deploy exactly this, or tell me what to change.
```

## Step 3a. Deploy on Latitude.sh (Latitude.sh MCP server)

1. `projects-list`. Use the project the user picks. Only call `projects-create` if they ask for a new one.
2. `SSH-keys-list` for that project. If their key is missing and they have given the public key, add it with `SSH-keys-create` (name it clearly, for example `gpu-finder-<user>`), after telling them.
3. Re-check stock: `plans-list` with `filterSlug` and `filterLocation`, and confirm the site is in `in_stock`. For GPU VMs use `plans-vm-list` with `filterGpu: true`. If it is no longer in stock, stop and offer the next best site.
4. `operating-systems-list-plans` to confirm the OS slug is valid for the plan.
5. Show the plan and wait for confirmation.
6. Create:
   - Bare metal, `servers-create`:
     ```json
     { "data": { "type": "servers", "attributes": {
         "project": "<project id or slug>", "plan": "g4.rtx6kpro.large", "site": "<site code>",
         "operating_system": "ubuntu24_ml_in_a_box", "hostname": "gpu-finder-rtx6k-01",
         "ssh_keys": ["<ssh key id>"], "billing": "hourly" } } }
     ```
   - GPU VM, `virtual-machines-create`:
     ```json
     { "data": { "type": "virtual_machines", "attributes": {
         "name": "gpu-finder-vm-01", "plan": "<vm plan slug>", "site": "<site code>",
         "project": "<project id>", "operating_system": "<os slug>", "ssh_keys": ["<ssh key id>"],
         "billing": "hourly" } } }
     ```
7. Poll `servers-get` (or `virtual-machines-get`) every 30 to 60 seconds until the status shows the server is on and it has a `primary_ipv4`. Bare metal usually deploys in minutes when the site is in stock, longer otherwise.
8. Report: server ID, hostname, site, IP, `ssh ubuntu@<ip>` (the user may differ by OS), hourly price, and how to delete it from the dashboard (https://www.latitude.sh/dashboard) when finished.

## Step 3b. Deploy on AWS (AWS MCP Server)

Use `scripts/aws_gpu_deploy.py`. Read it, set its `DEFAULTS` block, and pass the full script to `aws___run_script`, one action per run. If `aws___run_script` is unavailable, run the same script in the terminal with the user's AWS profile (same action names as command line arguments).

On-Demand:

1. `action: "plan"` with region, instance type, optional AZ, key name and ssh_cidr. It resolves the AMI, subnet, security group, key pair and vCPU quota, and changes nothing.
2. If `quota_ok` is false, stop. Explain that the account needs a quota increase for the P or G family (Service Quotas console) and do not attempt the launch.
3. `action: "launch"` with `confirm: False`. This is an EC2 DryRun that proves permissions and parameters. Combine its output with the On-Demand price from the finder into the plan and show it.
4. After the user confirms, run `action: "launch"` with `confirm: True`. It imports the key if needed, creates the `gpu-finder-ssh` security group (SSH from the user's IP only) if missing, and launches with IMDSv2 required, a 200 GB gp3 root volume and the `created-by=global-gpu-finder` tag.
5. `action: "status"` until the instance is running with a public IP. Report instance ID, IP, `ssh -i <key> ubuntu@<ip>`, hourly price, and the terminate command the script returns.

Capacity Block (guaranteed dates, for P5, P5e, P5en, P6-B200, P6-B300 and similar):

1. `action: "cb-offers"` with instance type, count, hours and days_ahead. Show the offers: dates, AZ, upfront fee.
2. The user picks one offer. Show the plan with the upfront fee and state clearly that it is charged immediately and cannot be cancelled. Get the double confirmation from the guardrails.
3. `action: "cb-buy"` with `offer_id` and `confirm: True`. Report the reservation ID and start time.
4. When the block is active, `action: "cb-launch"` with `reservation_id`, first with `confirm: False` (DryRun), then with `confirm: True` once the user says go. The block ends at its end time and instances are terminated by AWS, so remind the user to copy data off beforehand.

Limits to mention when relevant: the script sets up single instance networking only. Multi-node training on P5 or P6 needs EFA interfaces and a cluster placement group, which is a separate piece of work.

## Step 4. After deployment

- Summarise what was created, where, the hourly cost and how to stop billing.
- Offer the obvious next steps: attach storage, set up a private network, or connect Latitude.sh servers privately to the user's AWS VPC with Cloud Gateway (https://www.latitude.sh/cloud-gateway).
- If the user later asks to tear down, list the resources this power created (`status` on AWS, `servers-list` filtered by the `gpu-finder-` hostname prefix on Latitude.sh), confirm the exact IDs with them, then delete (`servers-delete`, `virtual-machines-delete`, or `aws ec2 terminate-instances`).

## Troubleshooting

- Latitude.sh `servers-create` returns an error about stock: the site sold out between the check and the deploy. Offer the next in-stock site.
- Latitude.sh returns 403: the signed-in user or API key cannot create servers in that project (read-only keys cannot deploy). See `setup-gpu-finder`.
- AWS `InsufficientInstanceCapacity`: AWS has no On-Demand capacity for that type in that AZ right now. Offer another AZ or region from the finder results, or a Capacity Block.
- AWS `VcpuLimitExceeded`: quota too low. The user must request an increase.
- AWS `UnauthorizedOperation` on a DryRun: the profile is missing an action from `references/aws-deploy-policy.md`.
- AWS `InvalidKeyPair.NotFound`: give the key pair name that exists in that region, or the public key to import.

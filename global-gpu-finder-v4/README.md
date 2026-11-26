# Global GPU Finder for Kiro

A Kiro power that helps you choose the right compute for an AI workload, finds GPU capacity worldwide across Latitude.sh and Amazon EC2, then deploys the option you pick. Ask in plain language and get one table showing which regions have the GPU you need, how available it is and what it costs per GPU hour. When you are ready, it prepares a priced deployment plan and launches it once you confirm.

Examples:

- "Which instance type should I use to serve a 70B model to 32 users with 32k context?"
- "Do I even need GPUs for this, or should I use Bedrock?"
- "Where can I get 8x B300 in the next week?"
- "Which regions have RTX PRO 6000 in stock right now, Latitude.sh or AWS?"
- "Compare H100 options in Europe on price per GPU hour."
- "Deploy the cheapest in-stock RTX PRO 6000 option for me."
- "Book a 2 day Capacity Block of p6-b300 in us-east-1 next week."

## What it uses

| Provider | How | Auth |
|---|---|---|
| Latitude.sh | Latitude.sh MCP server, hosted at https://mcp.latitude.sh/mcp (source: https://github.com/latitudesh/latitudesh-mcp) | OAuth sign-in in the browser, no key stored |
| AWS | AWS MCP Server through `mcp-proxy-for-aws` | Your local AWS credentials (profile, SSO or role) |

Searching is read only. Deploying only happens when you ask for it, after the power re-checks stock, shows you a plan with the price, and you reply to confirm. Anything it creates is tagged or named so you can find it later.

## How a session flows

1. Assess the workload. The power asks a few questions about your model, context, users, latency and traffic, and shows the maths. No sign-in needed.
2. Choose an option. You get a shortlist (which might be Amazon Bedrock, CPU, or a specific GPU class) and pick one.
3. Choose a provider: Latitude.sh, AWS or both.
4. Sign in to that provider only. Latitude.sh opens a browser sign-in; AWS uses a profile already on your machine, and the power reads back the account ID for you to confirm. You never type credentials into the chat.
5. Find live stock and prices for your chosen option.
6. Deploy, after a priced plan and your confirmation.

## Choosing the right instance

The `choose-instance` skill runs the selection framework from the AWS re:Invent 2026 session "Beyond the GPU count" as a short conversation:

1. Triage: does this need GPUs at all? If a managed model meets your latency target, traffic is spiky, or there is no one to run GPUs, it points you to Amazon Bedrock or another token-priced API. If the workload is small or not generative, it points you to CPU instances.
2. Start with the workload: model, precision, context, peak concurrency, latency SLO, traffic shape, data location and team.
3. Show the maths: weights plus KV cache, computed by `skills/choose-instance/scripts/sizing.py`, so you can check every number.
4. Walk the decision flow: one GPU (data parallel on PCIe GPUs), one node (tensor parallel on NVLink), or multi-node (shrink it first).
5. Traffic shape and cost per token decide how to buy: dedicated capacity for steady load, On-Demand, Capacity Blocks or Bedrock for spiky load.
6. A skeptic check ("but what about...") and a shortlist of two or three options. You decide; the power can then search live capacity for the shortlist.

Try the sizing maths on its own:

```bash
python3 skills/choose-instance/scripts/sizing.py memory --preset llama-70b --weights fp8 --kv fp8 --context 32k --concurrency 32
python3 skills/choose-instance/scripts/sizing.py cost --price 64 --tps 20000 --utilisation 0.3
```

## What it checks

Latitude.sh: GPU bare metal plans and GPU VMs, per site stock (`in_stock`, stock level, deploys instantly) and regional pricing.

AWS: every enabled region for matching GPU instance types (discovered live, so new families show up automatically), Availability Zones, On-Demand list price, Capacity Block offers (real reservable capacity), spot placement scores and, optionally, your P and G vCPU quotas.

Supported GPU keys: b300, gb300, b200, gb200, h200, h100, a100, rtx6000pro, l40s, l4, a10g, t4.

## Deploying

Latitude.sh: Metal GPU servers (`servers-create`) and GPU VMs (`virtual-machines-create`) in the project you choose, with your SSH key, hourly billing by default.

AWS: On-Demand instances (EC2 DryRun first, then launch with the Deep Learning Base GPU AMI, your key pair and an SSH rule limited to your IP), or Capacity Blocks for guaranteed dates. Capacity Blocks are paid upfront and cannot be cancelled, so the power asks twice before buying one.

AWS deploys need the extra IAM policy in `skills/deploy-gpu/references/aws-deploy-policy.md`. Latitude.sh deploys need a login or API key that can create servers in the project.

## Install

1. Install uv so `uvx` is available: https://docs.astral.sh/uv/getting-started/installation/
2. Make sure your AWS credentials work (`aws sts get-caller-identity`). Attaching the read-only IAM policy in `skills/find-gpu-capacity/references/aws-capacity-signals.md` is recommended.
3. In Kiro, open the Powers panel and choose to import a power from a folder (select this folder) or from GitHub (paste the repo URL once it is pushed).
4. Ask a GPU question. Kiro activates the power from keywords such as "which gpu", "gpu sizing", "find gpu" or "b300". The power sizes the workload first and asks you to sign in only once you have chosen a provider.

## Structure

```
global-gpu-finder/
├── plugin.json
├── mcp.json
└── skills/
    ├── choose-instance/
    │   ├── SKILL.md
    │   ├── references/
    │   │   └── framework.md
    │   └── scripts/
    │       └── sizing.py
    ├── find-gpu-capacity/
    │   ├── SKILL.md
    │   ├── references/
    │   │   ├── sku-map.md
    │   │   ├── latitude-plans.md
    │   │   └── aws-capacity-signals.md
    │   └── scripts/
    │       ├── aws_gpu_sweep.py
    │       └── latitude_gpu_sweep.py
    ├── deploy-gpu/
    │   ├── SKILL.md
    │   ├── references/
    │   │   └── aws-deploy-policy.md
    │   └── scripts/
    │       └── aws_gpu_deploy.py
    └── setup-gpu-finder/
        └── SKILL.md
```

## Running the scripts without Kiro

```bash
# AWS (needs boto3 and AWS credentials)
python3 skills/find-gpu-capacity/scripts/aws_gpu_sweep.py --gpu b300 rtx6000pro --format table

# Latitude.sh (needs a read-only API key)
export LATITUDESH_BEARER=<key>
python3 skills/find-gpu-capacity/scripts/latitude_gpu_sweep.py --gpu b300 rtx6000pro --format table

# AWS deploy: plan and DryRun first, add --confirm only when you mean it
python3 skills/deploy-gpu/scripts/aws_gpu_deploy.py plan --region us-east-1 --instance-type g7e.2xlarge --key-name my-key --ssh-cidr 203.0.113.10/32
python3 skills/deploy-gpu/scripts/aws_gpu_deploy.py launch --region us-east-1 --instance-type g7e.2xlarge --key-name my-key --ssh-cidr 203.0.113.10/32
```

## Security

- No credentials live in this repo. Latitude.sh uses OAuth through the hosted MCP server, and AWS uses your local credential chain (profile, SSO or role) through `mcp-proxy-for-aws`.
- The optional scripts read secrets only from the environment (`LATITUDESH_BEARER`) or the standard AWS chain. Nothing is written to disk.
- If you use the local Latitude.sh MCP option, the API key goes in your own Kiro user config (`~/.kiro/settings/mcp.json`), never in this repo's `mcp.json`. `.gitignore` covers common secret files as a backstop.
- Use a read-only Latitude.sh key and the read-only IAM policy for searching. Add deploy permissions only where needed, ideally on a separate AWS profile.
- IP addresses in examples (203.0.113.x) are from the reserved documentation range.

## Notes

- AWS does not publish live On-Demand stock. "Offered" means the type is sold in that region, not that capacity is free right now. Capacity Block offers and spot placement scores are the best available signals and are labelled as such.
- Latitude.sh `in_stock` reflects servers ready to deploy at that site when queried.
- Stock and prices change quickly, which is why the deploy step re-checks right before showing the plan.
- Deployed servers bill until you delete them. The power reports how to stop billing after every deploy.
- Multi-node GPU clusters (EFA, placement groups, private networks across many servers) are out of scope for now.

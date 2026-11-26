---
name: "setup-gpu-finder"
description: "Set up and troubleshoot the Global GPU Finder power: sign in to the Latitude.sh MCP server, configure AWS credentials and IAM policies (read-only for finding, optional deploy policy) for the AWS MCP Server, and verify both work. Use on first use, when a Latitude.sh or AWS tool call fails with an auth or connection error, or when the user asks how to configure the power."
compatibility: "Kiro. Needs uv (for uvx), an AWS account with credentials configured locally, and a Latitude.sh account."
metadata:
  author: "Latitude.sh"
  version: "1.0.0"
---

# Set up the Global GPU Finder power

## When to connect

Connect a provider only after the workload assessment (`choose-instance`) is done and the user has chosen where to run: Latitude.sh, AWS or both. Connect only what they chose, at that moment. Never ask the user to type a password, API key, access key or secret into the chat: Latitude.sh signs in through the browser, and AWS uses credentials already on their machine.

## Connect the chosen provider

Latitude.sh:

1. Tell the user: "Next I'll connect to Latitude.sh. A sign-in window will open in your browser; approve access there and come back."
2. Call `regions-get` (page size 100). If Kiro has not signed in yet, this triggers the browser sign-in.
3. When it returns locations, confirm in one line that Latitude.sh is connected. Use `projects-list` to show which team's projects they can see, so they know it is the right account.

AWS:

1. Ask which AWS profile to use (the name only, for example `default` or `gpu-search`). For deploying, suggest a separate profile with the deploy policy.
2. If they use SSO or the session may have expired, ask them to run `aws sso login --profile <name>` in their own terminal. If the profile is not the default, they either start Kiro with `AWS_PROFILE=<name>` or add `"--profile", "<name>"` to the `args` of `aws-mcp` in the installed power's `mcp.json` and restart that server from Kiro's MCP panel.
3. Confirm identity with `aws___run_script` (pass `aws_profile` if they named one):
   ```python
   import boto3
   i = boto3.client("sts").get_caller_identity()
   print({"account": i["Account"], "arn": i["Arn"]})
   ```
4. Read the account ID and identity back to the user and ask them to confirm it is the right account before any search or deploy.

Both: connect Latitude.sh first (browser), then AWS, confirming each.

## Prerequisites checklist

Only for the provider the user chose:


- [ ] A Latitude.sh account (sign up at https://www.latitude.sh)
- [ ] An AWS account and credentials on this machine (`aws configure`, `aws sso login`, or environment variables)
- [ ] `uv` installed so `uvx` is on the PATH (https://docs.astral.sh/uv/getting-started/installation/)
- [ ] Python 3.9+ with `boto3` if the user wants to run the local fallback scripts (`pip install boto3`)

## 1. Latitude.sh MCP server

The power connects to Latitude.sh's hosted MCP server at `https://mcp.latitude.sh/mcp`. It uses OAuth, so no API key is stored in the power.

1. The first time a Latitude.sh tool is called, Kiro opens a browser sign-in.
2. The user approves access in Latitude.sh and returns to Kiro.
3. Verify by calling `regions-get`. A list of locations means it works.

Alternative for users who prefer an API key or whose network blocks the hosted server: run the same MCP server locally, limited to the tools this power needs. Tell the user to add this to their own Kiro user config (`~/.kiro/settings/mcp.json`), never to the power's `mcp.json`, with an API key from https://www.latitude.sh/dashboard/api-keys. For finding only, use a read-only key and just the first five tools. To deploy, the key must be allowed to create servers and the extra tools below must be included:

```json
{
  "mcpServers": {
    "latitudesh-local": {
      "command": "npx",
      "args": [
        "-y", "latitudesh", "start",
        "--tool", "plans-list",
        "--tool", "plans-get",
        "--tool", "plans-vm-list",
        "--tool", "regions-get",
        "--tool", "regions-fetch",
        "--tool", "projects-list",
        "--tool", "SSH-keys-list",
        "--tool", "SSH-keys-create",
        "--tool", "operating-systems-list-plans",
        "--tool", "servers-create",
        "--tool", "servers-get",
        "--tool", "servers-list",
        "--tool", "virtual-machines-create",
        "--tool", "virtual-machines-get"
      ],
      "env": { "LATITUDESH_BEARER": "<API key>" }
    }
  }
}
```

## 2. AWS MCP Server

The power runs the AWS MCP Server through `mcp-proxy-for-aws`, which signs requests with the standard AWS credential chain (environment variables, `~/.aws/credentials`, SSO, or an IAM role).

1. Choose a profile. To use a non-default profile, set `AWS_PROFILE` before starting Kiro, or edit the installed power's `mcp.json` to add `"--profile", "<name>"` to the `args` list.
2. Attach the read-only policy in [AWS capacity signals](../find-gpu-capacity/references/aws-capacity-signals.md) to the role or user behind that profile. That is all the finder needs.
3. Only if the user wants to deploy from Kiro, also attach the deploy policy in [AWS deploy policy](../deploy-gpu/references/aws-deploy-policy.md). A separate profile for deploying is a good habit. Without it, the finder still works and deploy attempts fail safely with `UnauthorizedOperation`.
4. Verify by calling `aws___list_regions`, then run a small sweep: `scripts/aws_gpu_sweep.py` with `gpu: ["h100"]` and `regions: ["us-east-1"]` through `aws___run_script`.

There is no extra charge for the AWS MCP Server. The API calls used here are free describe and pricing calls.

## 3. Quick end-to-end test

Ask: "Which GPU should I use to serve a 70B model to 32 users with 32k context?"

A working setup runs the questionnaire, shows the memory maths and a shortlist, asks which provider to use, connects only that provider, then returns live stock and prices for the shortlisted GPUs.

## Troubleshooting

### Latitude.sh: sign-in window never appears or returns 401
Remove and re-add the power, or clear the Latitude.sh server's saved auth in Kiro's MCP panel, then call a tool again. If the hosted server is blocked on the network, use the local alternative above.

### AWS: `uvx: command not found`
Install uv, then restart Kiro so the PATH is picked up.

### AWS: `ExpiredToken` or `Unable to locate credentials`
Run `aws sso login --profile <name>` or refresh the credentials, then restart the AWS MCP server from Kiro's MCP panel.

### AWS: `AccessDenied` on one call
Add the missing action from the read-only policy (or the deploy policy, for deploy actions). The sweep reports which call failed and carries on with the rest.

### AWS: `aws___run_script` not available or script rejected
Run `scripts/aws_gpu_sweep.py` locally in the terminal with the same profile. It needs `boto3`.

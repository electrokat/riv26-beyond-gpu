---
name: "choose-instance"
description: "Start here for any GPU or compute question. Guide the user through a short questionnaire to pick the right compute for an AI workload, using the Beyond the GPU count framework: managed token APIs such as Amazon Bedrock, CPU instances, or the right GPU class (PCIe or NVLink, single GPU, single node or multi-node) on Latitude.sh or AWS, with the memory maths and cost per token shown. Then ask which provider to proceed with (Latitude.sh, AWS, both, or a managed API) and only then have them sign in to that provider to find and deploy GPUs. Use when the user asks which GPU or instance type to use, how many GPUs they need, whether a model fits, whether to self-host or use an API, or asks to find or deploy GPUs without having sized the workload in this conversation."
compatibility: "Kiro. Uses scripts/sizing.py (Python 3.9+, standard library only). Hands over to find-gpu-capacity and deploy-gpu in this power."
metadata:
  author: "Latitude.sh"
  version: "1.3.0"
---

# Choose the right instance for an AI workload

## Overview

This skill is the power's starting point. The order is always: assess the workload, choose an option, choose a provider, sign in to that provider, then find capacity and deploy.

It runs the selection framework from the re:Invent 2026 session "Beyond the GPU count" as a conversation. It starts with the workload, not a spec sheet, and it is allowed to conclude that the user should not run GPUs at all.

Read [the framework reference](references/framework.md) before starting. Keep its wording: workload first, then server trade-offs; fit, pattern, utilisation; cost per token last.

Rules for the conversation:

- Ask one or two questions at a time, in plain language, with a sensible default the user can accept by saying "default" or "not sure".
- If the user pastes a full workload profile (for example "70B, FP8, 32 concurrent users, 32k context, p95 TTFT under 1 s, APAC, steady"), skip the questions they have already answered.
- Show the maths. Every recommendation states its memory and KV assumptions so a human can check them in ten seconds. Run `scripts/sizing.py` rather than doing arithmetic in your head.
- Recommend, never decide. End with a shortlist of two or three options and hand the decision back.
- Read only. This skill never creates anything.
- No sign-in during the assessment. Do not call any Latitude.sh or AWS tools in Stages 0 to 6. The user has not chosen a provider yet, and nobody should be asked to sign in before they know which provider they want.

## Stage 0. Triage: does this need GPUs at all?

Ask:

1. What are you building, and which model (or kind of model) does it use?
2. Do you need to run your own weights (fine-tuned, custom, or a model no managed service offers), or would calling a hosted model through an API be acceptable?
3. Roughly how much traffic: tokens or requests per day, and is it steady, busy in business hours, or spiky?
4. Any hard constraints on where data can be processed?

Route using the Step 0 table in the framework reference:

- Managed token API is the best fit when: a hosted model meets their quality and latency needs, traffic is spiky or modest, they have no custom weights (or a supported architecture that Bedrock Custom Model Import can run), or the team has no one to own GPU operations. Recommend Amazon Bedrock first (it sits in their AWS account, IAM and data controls, and AgentCore Gateway can front it next to self-hosted models later). Mention that direct model-provider APIs and other serverless inference services are equivalent options. Offer to compare cost: ask for their expected monthly tokens and the API's current per-token prices (look them up from the provider's pricing page; do not quote prices from memory), then run `sizing.py cost` with `--monthly-tokens --api-in --api-out` against a candidate instance. Stop there unless they want to self-host anyway.
- CPU is the best fit when: there is no generative model, or the model is small (classic ML, a small embedding model at low volume) and latency is relaxed. Recommend CPU compute on Latitude.sh (Metal or VMs) or EC2 general purpose or compute optimised instances (including Graviton), and suggest load testing on one before sizing a fleet. This power's finder only searches GPUs, so say so.
- Self-hosting GPUs is worth it when: steady, high volume keeps GPUs busy; they need custom weights or a model no managed service runs; data residency or isolation rules out a shared API; or they need latency or throughput a shared service cannot guarantee. Continue to Stage 1.

Say which branch you chose and why in one or two sentences, so the user can disagree.

## Stage 1. Start with the workload

Collect the eight fields from the framework. Ask in this order, with these defaults:

| Field | Question | Default if unsure |
|---|---|---|
| Model | Which model, how many parameters, dense or mixture of experts? | Ask; for a known model read its config.json (`num_hidden_layers`, `num_key_value_heads`, `head_dim`) |
| Precision | Weights and KV cache precision? | FP8 weights, FP8 KV cache, with a note to validate quality on their evals |
| Context | Typical and maximum context per request? | 8k maximum |
| Concurrency | Requests in flight at peak (not average)? | 32 |
| Latency SLO | p95 time to first token and inter-token latency? | TTFT under 1 s, inter-token under 50 ms, flagged as an assumption to load test |
| Traffic shape | Steady, diurnal or spiky? Can it scale to zero? | Diurnal |
| Data and location | Where does the data live and where are the callers? | Ask; it narrows regions |
| Team | Who is on call, and what do they already run (Kubernetes, EKS, bare metal)? | Ask; it can change the answer |

For mixture of experts models, use the total parameter count for memory, because every expert sits in memory.

## Stage 2. Show the memory maths

Run:

```bash
python3 scripts/sizing.py memory --preset llama-70b --weights fp8 --kv fp8 --context 32k --concurrency 32
# or: --config path/to/config.json --params <billions>, or --params --layers --kv-heads --head-dim
```

Show the user the weights, KV per token, KV cache, total, and the per-GPU table. Call out when the KV cache is bigger than the weights: that means context and concurrency, not the model, are driving the hardware.

## Stage 3. Walk the decision flow

Apply the flow from the framework reference and say each step out loud:

1. Fits on one GPU? Then data parallel replicas on cost-efficient PCIe GPUs (L4, L40S, RTX PRO 6000; AWS G6, G6e, G7e; Latitude.sh g4.rtx6kpro.large or GPU VMs). Do not pay for NVLink they will not use.
2. Fits in one node? Then tensor parallel inside an NVLink node (H200, B200, B300; AWS P5en, P6-B200, P6-B300; Latitude.sh g4.b300.large). If the cheapest fit is tensor parallel over PCIe, say it works at modest concurrency but inter-token latency suffers and must be measured against their SLO.
3. Multi-node? First ask whether FP8 or FP4, or expert parallel for MoE, brings it back into one node. Rerun the sizing to show it. Only then talk about RDMA fabrics (EFA on AWS, InfiniBand or RoCE on bare metal), and flag it as a bigger cost and operations decision.

Then name the serving pattern from the jobs table (data, tensor, pipeline, expert parallel, disaggregated prefill and decode, or batch) and what it means for the hardware. If their product mixes jobs, suggest separate pools under one scheduling plane.

Mention Inferentia2 or Trainium as additional options when the model is supported on them.

## Stage 4. Traffic shape, utilisation and cost per token

- Map traffic shape to buying model: steady goes on reserved or bare metal capacity that stays busy (Latitude.sh hourly or monthly Metal GPU, EC2 Savings Plans); spiky goes on On-Demand, Capacity Blocks, or Bedrock.
- If they have measured or can estimate throughput at their SLO, run `sizing.py cost` for the top two candidates with their utilisation, and if relevant compare against a managed API. If they have no throughput numbers, say so plainly, use the talk's illustrative figures only to show the shape, and recommend a short load test (the talk's Demo 1 method: same engine, same model, step up concurrency, watch KV cache usage and queue depth, read tokens per second at the load where p95 still meets the SLO).
- For prices during the assessment, use the providers' public list prices (https://www.latitude.sh/pricing, the AWS EC2 On-Demand pricing page, the Amazon Bedrock pricing page), labelled as list prices with the date, or numbers the user gives you. Do not quote prices from memory. Live stock and regional pricing come after sign-in, in Stage 8.

## Stage 5. Skeptic check ("but what about...")

Before recommending, ask yourself the inner-skeptic questions from the talk and tell the user the two or three that matter most for their case:

- What happens if context or concurrency doubles? Rerun the sizing with the larger numbers and show whether the recommendation still holds.
- Has the lower precision been validated on their own evals?
- Is the GPU actually available this quarter in the region their data lives in?
- Who gets paged at 2am, and do they know how to run this?
- Would a managed API meet the SLO and make all of this unnecessary?

## Stage 6. The recommendation card

Present this, filled in. Plain text, no marketing.

```
Workload profile
Model:          <name, params, dense/MoE>     Precision: weights <x>, KV <y>
Context:        <max> tokens                  Concurrency: <peak>
Latency SLO:    TTFT <x>, ITL <y> (p95)       Traffic: <shape>
Location:       <data and callers>            Team: <who runs it>

The maths
Weights <a> GB + KV cache <b> GB = <c> GB, about <d> GB with overhead

Verdict
Branch:   managed API | CPU | GPU
Fit:      one GPU | one node | multi-node
Pattern:  <serving pattern>
Buy as:   <reserved/bare metal | On-Demand | Capacity Blocks | Bedrock>

Shortlist (you decide)
1. <option, provider, why, what to watch>
2. <option>
3. <option, often the managed API or the cheaper box at lower concurrency>

Before you commit
- <top skeptic check>
- Load test at peak concurrency and read tokens per second at your SLO
```

Offer to save it: "Want this saved as gpu-workload-profile.md in your project so your team can review it?" Only write the file if they say yes.

## Stage 7. Choose the option and the provider

Ask the user to pick one option from the shortlist, then ask where they want to run it:

- Latitude.sh: dedicated bare metal GPU servers or GPU VMs, billed hourly.
- AWS: EC2 GPU instances, On-Demand or Capacity Blocks.
- Both: compare live stock and price side by side before deciding. This needs a sign-in to each.
- Managed API (if the verdict was Bedrock or another token API): no GPU search is needed. Point them to the Amazon Bedrock console and model catalogue, or the provider's API docs, and stop. If they later want to self-host, come back to Stage 7.
- CPU: this power only searches GPUs. Point them to Latitude.sh Metal or VMs, or EC2, and stop.

Confirm their choice back in one line ("Got it: 8x B300 on Latitude.sh, searching APAC first").

## Stage 8. Sign in to the chosen provider, then find and deploy

Only now connect, and only to the provider they chose. Follow "Connect the chosen provider" in the `setup-gpu-finder` skill:

- Latitude.sh: tell them the next step opens a Latitude.sh sign-in in their browser, then make the first Latitude.sh call (`regions-get`) to trigger it and confirm the connection.
- AWS: ask which AWS profile to use (the name only), have them sign in on their machine if needed (`aws sso login --profile <name>`), then confirm the account ID and identity with them before anything else.

Never ask the user to type a password, API key, access key or secret into the chat. Credentials stay in the browser sign-in or on their machine.

Once connected, hand over to `find-gpu-capacity` with the GPU keys, geography and timeframe from the assessment, limited to the chosen provider. Deploying happens only through `deploy-gpu`, with its priced plan and confirmation.

## Example

User: "We want to serve a 70B model at FP8 to about 32 users at a time with 32k context, steady traffic, APAC customers."

1. Triage: custom serving of a 70B model with steady traffic and APAC data. Self-hosting is reasonable, but note Bedrock as the comparison if the model is available there.
2. Sizing: 70 GB weights + 172 GB KV cache = 242 GB, about 266 GB with overhead. The KV cache is bigger than the weights.
3. Flow: does not fit one 96 GB GPU; fits one B300 GPU (288 GB); on RTX PRO 6000 it needs tensor parallel 4 over PCIe. Pattern: long-context tensor parallel, which wants NVLink.
4. Steady traffic suits dedicated capacity. Shortlist: Latitude.sh g4.b300.large or AWS P6-B300 with one replica per GPU; P5en (H200) as an alternative; 4x RTX PRO 6000 only if inter-token latency holds at 32 users.
5. Skeptic: if concurrency doubles to 64, rerun: KV cache doubles to 344 GB and no longer fits one B300 GPU.
6. The user picks 8x B300 and chooses Latitude.sh. Explain the browser sign-in, connect, confirm, then search live stock for b300 on Latitude.sh only.

## Troubleshooting

- The user does not know the architecture numbers: ask for the model name and read its config.json, or use the closest preset and label it as an estimate.
- The user only knows average concurrency: ask for peak, or assume 2 to 3 times average and say so.
- The numbers sit right on a boundary (utilisation of allocated memory above 0.9): flag it as tight and recommend the next size up or a lower KV precision.

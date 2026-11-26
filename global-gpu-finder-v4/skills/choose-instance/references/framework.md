# The "Beyond the GPU count" selection framework

Reference for the choose-instance skill. It follows the framework from the AWS re:Invent 2026 session "Beyond the GPU count" (Level 300). Use the same wording and order so people who saw the talk recognise it.

The core idea: GPU count is a vanity metric. Fit decides what is possible. Pattern decides what is efficient. Utilisation decides what is cheap.

## Step 0: do you need to hold GPUs at all?

The talk's anti-patterns slide, applied first:

| Signal | Point them to |
|---|---|
| The model they want is available as a managed model and meets their latency target | Amazon Bedrock (or another hosted model API). Stop here, do not build infrastructure. |
| Spiky traffic that should scale to zero | Bedrock or another token-priced API, or On-Demand / Capacity Blocks if they must self-host |
| Small team with no one to own GPU operations and a second network | Bedrock or another token-priced API |
| Fine-tuned weights of a supported architecture, but low or spiky volume | Bedrock Custom Model Import (supports popular architectures such as Llama, Mistral, Qwen and gpt-oss; check the current list), billed for use rather than for idle GPUs |
| Not generative AI at all, or very small models (classic ML, small embedding models at low volume, web and API tiers) | CPU instances: Latitude.sh Metal or VMs, or EC2 general purpose / compute optimised (including Graviton). AWS Inferentia2 is an option for supported models. |
| Disconnected or intermittent sites | Not this pattern. EKS Anywhere is AWS's answer for those. |

Rule from the talk: "If your workload already fits on one managed instance, or a Bedrock model meets your SLO, stop. You are done."

Self-hosting starts to make sense when one or more of these is true: steady, high token volume that keeps GPUs busy; custom or fine-tuned weights that a managed service cannot run; strict data residency or isolation; latency or throughput a shared API cannot guarantee; or a model not offered as a managed service.

## Step 1: start with the workload (fill this in before opening any catalogue)

| Field | What to capture | Why it matters |
|---|---|---|
| Model | Parameters, dense or mixture of experts, layers, KV heads, head dim | Weights size and KV cache per token |
| Precision | Weights and KV cache, separately (FP16, FP8, FP4) | FP8 halves memory, FP4 quarters it. Quality is measured on their evals, never assumed |
| Context | Typical and maximum, input and output | The maximum sizes the cache |
| Concurrency | Requests in flight at peak, not average | Multiplies the cache |
| Latency SLO | Time to first token and inter-token latency at p95, from a load test | Decides batch size and whether a cheaper box can keep up |
| Traffic shape | Steady, diurnal or spiky. Can it scale to zero? | Decides how to buy, and whether dedicated hardware pays off |
| Data and location | Where the data lives, sovereignty rules, where callers are | Narrows regions and providers |
| Team | Who is paged at 2am and what they already run | The most expensive question, even though it is not a hardware one |

## Memory maths

```
weights (GB)     = parameters (billions) x bytes per parameter     FP16 = 2, FP8 = 1, FP4 = 0.5
KV per token (B) = 2 x layers x KV heads x head dim x KV bytes
KV cache (GB)    = KV per token x context x concurrent requests / 1e9
needed (GB)      = (weights + KV) x (1 + runtime overhead, about 10%)
```

Worked example from the talk (Llama-class 70B, 80 layers, 8 KV heads, head dim 128, 32 users):

| Case | Weights | KV cache | Total | RTX PRO 6000 (96 GB) | B300 (288 GB) |
|---|---|---|---|---|---|
| FP16, 8k context | 140 GB | 86 GB | 226 GB | 3 GPUs of memory, 4 in practice (TP is a power of two) | 1 GPU |
| FP8, 8k context | 70 GB | 43 GB | 113 GB | 2 GPUs | 1 GPU |
| FP8, 32k context | 70 GB | 172 GB | 242 GB | 4 GPUs | 1 GPU |
| FP4 weights, FP8 cache, 8k | 35 GB | 43 GB | 78 GB | 1 GPU | 1 GPU |

GPU count does not appear anywhere in the calculation. Precision, context and concurrency decide everything. `scripts/sizing.py memory` reproduces these numbers.

## Step 2: weigh the server trade-offs, in this order

1. Memory per GPU. Decides fit and tensor parallel size.
2. GPU interconnect. NVLink versus PCIe only matters for tensor parallel, expert parallel or very large batches. NVLink 5 on B300 is 1.8 TB/s per GPU; PCIe Gen5 x16 is about 64 GB/s each way, roughly 14 times slower per GPU.
3. Node network. Only for more than one node per replica: EFA on AWS, InfiniBand or RoCE on bare metal.
4. Local storage. Fast NVMe for weights decides cold start and recovery. 140 GB of weights takes about 15 minutes over a 1.25 Gbps VPN tunnel and about 20 seconds from local NVMe.
5. Availability. On AWS: On-Demand, Capacity Blocks for ML, Savings Plans. On bare metal: stock and lead times. (The find-gpu-capacity skill checks this live.)
6. Cost per token, last. Price only means anything once you know the box can do the job.

## The decision flow

```
Q1  Do weights + KV cache at peak fit on ONE GPU?
    yes -> data parallel replicas on cost-efficient GPUs
           L4, L40S, RTX PRO 6000 | AWS G6, G6e, G7e | Latitude.sh g4.rtx6kpro.large or GPU VMs
           Do not pay for NVLink you will not use.
    no  -> Q2
Q2  Does it fit inside ONE node (8 GPUs)?
    yes -> tensor parallel inside an NVLink node
           H200, B200, B300 | AWS P5en, P6-B200, P6-B300 | Latitude.sh g4.b300.large
    no  -> multi-node. First ask: can quantisation or expert parallel shrink it back into one node?
           If not: RDMA fabric (EFA on AWS, InfiniBand or RoCE on bare metal). A separate cost and operations conversation.
Q3  Across all three, traffic shape decides how you buy:
    steady  -> reserved or bare metal capacity that stays busy (cheapest per token)
    spiky   -> On-Demand, Capacity Blocks, or Amazon Bedrock where you hold no GPUs at all
```

If the model is supported on AWS Inferentia2 or Trainium, add them to the Q1 and Q2 boxes and compare cost per token. The framework is the same; the silicon list grows.

Note: tensor parallel over PCIe (for example 2 or 4 RTX PRO 6000s) works and can be the right economic choice at modest concurrency, but inter-token latency suffers. Measure it rather than assume it.

## Serving patterns and the jobs that use them

| Job | Pattern | Hardware it wants |
|---|---|---|
| Chat assistant on an 8B to 32B model, many independent users | Data parallel | PCIe GPUs: L40S, RTX PRO 6000 (G6e, G7e) |
| Long-context 70B work (code, contracts, big documents) | Tensor parallel | NVLink: H200, B200, B300 |
| Huge dense model that will not fit one server even at FP8 | Pipeline parallel across nodes | RDMA fabric. Ask first whether quantisation brings it back to one node |
| Mixture of experts (for example DeepSeek-V3, gpt-oss) | Expert parallel, heavy all-to-all | NVLink nodes; every expert still sits in memory |
| RAG and agents: long prompts, short answers | Disaggregated prefill and decode (NVIDIA Dynamo, llm-d) | Separate pools sized for compute-bound prefill and bandwidth-bound decode |
| Batch: embeddings, evals, overnight document processing | Batch and offline | Cheapest tokens per dollar: Spot, Capacity Blocks, hourly bare metal |

One product often does three of these at once: run three pools under one scheduling plane (EKS Hybrid Nodes in the talk).

## Common workload shapes (starting points, not answers)

| Workload | Starting point |
|---|---|
| Chat assistant, 8B model | One GPU, data parallel, L4 or L40S (G6, G6e), or a managed model on Bedrock |
| RAG, 70B at FP8, 8k context | One large GPU or two mid-size: RTX PRO 6000 or H200 (G7e, P5en), Latitude.sh g4.rtx6kpro.large |
| Same 70B, 32k context, high concurrency | Tensor parallel in an NVLink node: H200, B200, B300 (P5en, P6), Latitude.sh g4.b300.large |
| Large MoE, hundreds of billions of params | Expert plus tensor parallel, ideally disaggregated: B300 or Grace Blackwell rack-scale (P6-B300, P6e-GB200 UltraServers) |
| Offline batch or embeddings | Cheapest tokens: Spot, Capacity Blocks, hourly bare metal |

"The GPU names will change. The columns will not."

## The only cost number that matters

```
cost per 1M tokens = hourly price / (tokens per second at SLO x 3600) x 1,000,000
effective cost     = cost per 1M tokens / utilisation
```

Illustrative throughputs from the talk (made up to show the shape, measure your own):

| Box | Hourly | Throughput | Busy | Cost per 1M tokens |
|---|---|---|---|---|
| 8x RTX PRO 6000 | 24 | 5,000 tok/s | 100% | about 1.33 |
| 8x B300 | 64 | 20,000 tok/s | 100% | about 0.89 |
| 8x B300, spiky traffic | 64 | 20,000 tok/s | 30% | nearly 3 |

The expensive box can be cheaper per token if you keep it busy. That is why steady baselines go on dedicated hardware and spiky load goes somewhere you only pay while it runs.

For managed APIs compare directly: monthly tokens x blended price per 1M tokens, versus instances needed x hourly price x 730. `scripts/sizing.py cost` does both and prints the break-even volume.

## Guardrails (from "Guardrails we built into our GPU agent")

- Read-only by default. Sizing and finding never need write access.
- Show the maths. Every recommendation states its memory and KV assumptions so a human can check them in ten seconds.
- Priced plan first, explicit confirm, ask twice for big spend (handled by deploy-gpu).
- Agents shortlist and show their working. Engineers decide.

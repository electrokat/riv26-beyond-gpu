# GPU SKU map: Latitude.sh and Amazon EC2

Use this table to turn whatever the user typed ("B300s", "rtx6000pro", "H200", "a Blackwell box") into the exact plans and instance types to query on each provider.

This table is a starting point, not the source of truth. Both providers add SKUs and regions often. Always confirm against live data:

- Latitude.sh: match on `attributes.specs.gpu.type` and `attributes.slug` from `plans-list` and `plans-vm-list`.
- AWS: the sweep script also discovers GPU instance types live from `DescribeInstanceTypes` (`GpuInfo.Gpus[].Name`), so new families appear even if they are missing here.

## Canonical GPU keys and aliases

| Key | Aliases users type | Match tokens (lower case, spaces and dashes removed) |
|---|---|---|
| b300 | B300, HGX B300, Blackwell Ultra | b300 |
| gb300 | GB300, GB300 NVL72 | gb300 |
| b200 | B200, HGX B200, Blackwell | b200 |
| gb200 | GB200, GB200 NVL72 | gb200 |
| h200 | H200 | h200 |
| h100 | H100, H100 SXM, H100 PCIe | h100 |
| a100 | A100 | a100 |
| rtx6000pro | RTX PRO 6000, RTX 6000 Pro, rtx6kpro, RTX PRO 6000 Blackwell Server Edition | rtxpro6000, rtx6000pro, rtx6kpro, rtxproserver6000 |
| l40s | L40S | l40s |
| l4 | L4 | l4 (exact word match only, so it does not catch l40s) |
| a10g | A10G, A10 | a10g, a10 |
| t4 | T4 | t4 (exact word match only) |

"Blackwell" on its own is ambiguous. Ask whether the user means B300, B200, GB200/GB300 or RTX PRO 6000, or search all of them.

## Latitude.sh (known SKUs at time of writing)

| GPU key | Plan slug | Type | GPUs | VRAM per GPU | Interconnect / network |
|---|---|---|---|---|---|
| b300 | g4.b300.large | Metal GPU (bare metal) | 8x NVIDIA HGX B300 | 288 GB | NVLink, 800 Gbps RoCE plus 2x 100 Gbps |
| rtx6000pro | g4.rtx6kpro.large | Metal GPU (bare metal) | 8x NVIDIA RTX PRO 6000 Server Edition | 96 GB | PCIe Gen5, 2x 100 Gbps |
| h100 | g3.h100.small | Metal GPU (bare metal) | 1x NVIDIA H100 PCIe | 80 GB | 2x 10 Gbps |

GPU VMs are listed separately by `plans-vm-list` with `filterGpu: true`. Custom GPU builds (larger clusters, other GPUs) go through Latitude.sh Build: https://www.latitude.sh/build or a sales conversation. If a user asks for a GPU that is not in the live plan list, say so and offer the Build route rather than saying Latitude.sh cannot do it.

## Amazon EC2 (known GPU instance types at time of writing)

| GPU key | Instance types | GPUs per instance | GPU memory | Notes |
|---|---|---|---|---|
| b300 | p6-b300.48xlarge | 8x B300 | 2,144 GB total | Capacity Blocks and On-Demand in selected regions |
| b200 | p6-b200.48xlarge | 8x B200 | 1,432 GB total | Capacity Blocks and On-Demand in selected regions |
| gb200 | p6e-gb200.36xlarge | 4x GB200 | 740 GB total | Only sold as UltraServers (u-p6e-gb200x36, u-p6e-gb200x72) via Capacity Blocks |
| h200 | p5en.48xlarge, p5e.48xlarge | 8x H200 | 141 GB per GPU | p5en has faster CPU and networking |
| h100 | p5.48xlarge, p5.4xlarge | 8x or 1x H100 | 80 GB per GPU | p5.4xlarge is the single GPU size |
| a100 | p4de.24xlarge, p4d.24xlarge | 8x A100 | 80 GB (p4de), 40 GB (p4d) per GPU | Older generation |
| rtx6000pro | g7e.2xlarge, g7e.4xlarge, g7e.8xlarge, g7e.12xlarge, g7e.24xlarge, g7e.48xlarge | 1, 1, 1, 2, 4, 8 | 96 GB per GPU | RTX PRO 6000 Blackwell Server Edition |
| l40s | g6e.* (xlarge to 48xlarge) | 1 to 8x L40S | 48 GB per GPU | |
| l4 | g6.*, gr6.* | 1 to 8x L4 | 24 GB per GPU | |
| a10g | g5.* | 1 to 8x A10G | 24 GB per GPU | |
| t4 | g4dn.* | 1 to 8x T4 | 16 GB per GPU | |

Trainium (trn1, trn2) and Inferentia (inf2) are AWS accelerators, not NVIDIA GPUs. Only include them if the user asks for "any accelerator".

## Like-for-like comparison tips

- Compare on price per GPU hour, then check VRAM per GPU and interconnect. An 8x RTX PRO 6000 box and an 8x B300 box have the same GPU count but very different memory bandwidth and NVLink support.
- Latitude.sh plans are whole dedicated servers. For EC2 families with several sizes (g7e, g6e), compare the size with the same GPU count as the Latitude.sh plan, and also show the smallest size if the user only needs one GPU.
- AWS prices vary by region. Latitude.sh prices can vary by region and currency (the API returns prices keyed by ISO currency code, for example USD or BRL). Default to USD unless the user asks otherwise.

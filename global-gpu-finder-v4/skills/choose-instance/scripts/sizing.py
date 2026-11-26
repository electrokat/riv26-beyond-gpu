#!/usr/bin/env python3
"""Size an LLM inference workload and compare cost per token.

Implements the "Beyond the GPU count" framework maths so every recommendation can show its working:

  weights_GB  = params (billions) x bytes per parameter
  kv_per_tok  = 2 (K and V) x layers x kv_heads x head_dim x kv bytes
  kv_GB       = kv_per_tok x context tokens x concurrent requests / 1e9
  required_GB = (weights_GB + kv_GB) x (1 + overhead)
  cost per 1M tokens = hourly price / (tokens per second x 3600) x 1,000,000 / utilisation

Standard library only. Two sub-commands:

  memory   python3 sizing.py memory --preset llama-70b --weights fp8 --kv fp8 --context 32k --concurrency 32
           python3 sizing.py memory --config ./config.json --params 70 --weights fp16 --context 8k --concurrency 32
  cost     python3 sizing.py cost --price 64 --tps 20000 --utilisation 0.3
           python3 sizing.py cost --price 24 --tps 5000 --monthly-tokens 2e9 --api-in 0.5 --api-out 1.5 --output-share 0.25

Prices are whatever you pass in. Nothing is looked up or hardcoded.
"""

import argparse
import json
import math
import sys

BYTES = {"fp32": 4, "bf16": 2, "fp16": 2, "fp8": 1, "int8": 1, "fp4": 0.5, "int4": 0.5}

# Architecture presets the talk uses. Anything else: pass --config (a Hugging Face config.json) or the numbers.
PRESETS = {
    "llama-8b": {"params": 8, "layers": 32, "kv_heads": 8, "head_dim": 128},
    "llama-70b": {"params": 70, "layers": 80, "kv_heads": 8, "head_dim": 128},
    "llama-405b": {"params": 405, "layers": 126, "kv_heads": 8, "head_dim": 128},
}

# GPU memory per GPU in GB and whether GPUs in a typical 8 GPU server share an NVLink domain.
# Chip figures; platforms can report slightly less usable memory (for example AWS lists p6-b300 at 2,144 GB per 8 GPUs).
GPUS = {
    "L4 (24 GB)": (24, False),
    "A10G (24 GB)": (24, False),
    "L40S (48 GB)": (48, False),
    "H100 (80 GB)": (80, True),
    "RTX PRO 6000 (96 GB)": (96, False),
    "H200 (141 GB)": (141, True),
    "B200 (180 GB)": (180, True),
    "B300 (288 GB)": (288, True),
}
GPUS_PER_NODE = 8


def tokens(v):
    v = str(v).strip().lower()
    if v.endswith("k"):
        return int(float(v[:-1]) * 1024)
    return int(float(v))


def from_config(path):
    with open(path) as f:
        c = json.load(f)
    c = c.get("text_config", c)
    layers = c["num_hidden_layers"]
    heads = c["num_attention_heads"]
    kv_heads = c.get("num_key_value_heads", heads)
    head_dim = c.get("head_dim") or c["hidden_size"] // heads
    return {"layers": layers, "kv_heads": kv_heads, "head_dim": head_dim}


def tp_size(n):
    """Tensor parallel sizes are powers of two in practice."""
    p = 1
    while p < n:
        p *= 2
    return p


def memory(a):
    arch = dict(PRESETS[a.preset]) if a.preset else {}
    if a.config:
        arch.update(from_config(a.config))
    for k in ("params", "layers", "kv_heads", "head_dim"):
        v = getattr(a, k)
        if v is not None:
            arch[k] = v
    missing = [k for k in ("params", "layers", "kv_heads", "head_dim") if k not in arch]
    if missing:
        raise SystemExit(f"Missing {', '.join(missing)}. Use --preset, --config or pass them directly.")

    ctx, conc = tokens(a.context), a.concurrency
    wb, kb = BYTES[a.weights], BYTES[a.kv]
    weights_gb = arch["params"] * wb
    kv_tok = 2 * arch["layers"] * arch["kv_heads"] * arch["head_dim"] * kb
    kv_gb = kv_tok * ctx * conc / 1e9
    raw = weights_gb + kv_gb
    need = raw * (1 + a.overhead)

    rows = []
    for name, (mem, nvlink) in GPUS.items():
        n = math.ceil(need / mem)
        tp = tp_size(n)
        if tp == 1:
            fit, pattern = "one GPU", "data parallel replicas"
        elif tp <= GPUS_PER_NODE:
            fit = f"{tp} GPUs, one node"
            pattern = "tensor parallel over NVLink" if nvlink else "tensor parallel over PCIe (measure inter-token latency)"
        else:
            fit, pattern = f"{tp} GPUs, multi-node", "pipeline or expert parallel over an RDMA fabric"
        rows.append({"gpu": name, "gpus_needed": n, "tp_size": tp, "fit": fit, "pattern": pattern,
                     "utilisation_of_allocated": round(need / (tp * mem), 2)})

    out = {
        "inputs": {**arch, "weights": a.weights, "kv": a.kv, "context_tokens": ctx, "concurrency": conc,
                   "overhead": a.overhead},
        "weights_gb": round(weights_gb, 1),
        "kv_bytes_per_token": kv_tok,
        "kv_gb": round(kv_gb, 1),
        "weights_plus_kv_gb": round(raw, 1),
        "required_gb_with_overhead": round(need, 1),
        "kv_bigger_than_weights": kv_gb > weights_gb,
        "per_gpu": rows,
        "notes": [
            "Use peak concurrency and maximum context, not averages.",
            "MoE models: params is the TOTAL parameter count, because every expert sits in memory.",
            "Lower precision is a quality trade-off. Measure it on your own evals before shipping.",
        ],
    }
    if a.format == "json":
        print(json.dumps(out, indent=2))
        return
    i = out["inputs"]
    print(f"Model: {i['params']}B params, {i['layers']} layers, {i['kv_heads']} KV heads, head dim {i['head_dim']}")
    print(f"Precision: weights {a.weights}, KV cache {a.kv}. Context {ctx:,} tokens x {conc} concurrent requests")
    print(f"Weights:        {out['weights_gb']:>8} GB  ({i['params']}B x {wb} bytes)")
    print(f"KV per token:   {kv_tok/1e6:>8.3f} MB  (2 x {i['layers']} x {i['kv_heads']} x {i['head_dim']} x {kb} bytes)")
    print(f"KV cache:       {out['kv_gb']:>8} GB  ({ctx * conc:,} tokens in flight)")
    print(f"Weights + KV:   {out['weights_plus_kv_gb']:>8} GB")
    print(f"With {int(a.overhead*100)}% overhead: {out['required_gb_with_overhead']:>6} GB")
    if out["kv_bigger_than_weights"]:
        print("Note: the KV cache is now bigger than the weights. Context and concurrency are driving the size.")
    print()
    w = max(len(r["gpu"]) for r in rows)
    print(f"{'GPU'.ljust(w)}  GPUs  TP  Fit                      Pattern")
    for r in rows:
        print(f"{r['gpu'].ljust(w)}  {r['gpus_needed']:>4}  {r['tp_size']:>2}  {r['fit'].ljust(23)}  {r['pattern']}")


def cost(a):
    tok_hr = a.tps * 3600
    per_m = a.price / tok_hr * 1e6
    eff = per_m / a.utilisation
    out = {"hourly_price": a.price, "tokens_per_second_at_slo": a.tps, "utilisation": a.utilisation,
           "cost_per_1m_tokens_busy": round(per_m, 3), "cost_per_1m_tokens_effective": round(eff, 3),
           "monthly_instance_cost": round(a.price * 730, 2)}
    if a.monthly_tokens:
        mt = float(a.monthly_tokens)
        out["monthly_tokens"] = mt
        out["instance_capacity_tokens_per_month"] = round(tok_hr * 730 * a.utilisation)
        out["instances_needed"] = max(1, math.ceil(mt / (tok_hr * 730 * a.utilisation)))
        out["monthly_self_hosted_cost"] = round(out["instances_needed"] * a.price * 730, 2)
        if a.api_in is not None and a.api_out is not None:
            blended = a.api_in * (1 - a.output_share) + a.api_out * a.output_share
            out["api_blended_per_1m"] = round(blended, 3)
            out["monthly_api_cost"] = round(mt / 1e6 * blended, 2)
            out["break_even_monthly_tokens"] = round(a.price * 730 / blended * 1e6)
            out["cheaper"] = "managed API" if out["monthly_api_cost"] < out["monthly_self_hosted_cost"] else "self-hosted"
    if a.format == "json":
        print(json.dumps(out, indent=2))
        return
    print(f"{a.price:.2f} per hour / ({a.tps:,} tok/s x 3600) x 1,000,000 = {per_m:.3f} per 1M tokens when busy")
    print(f"At {int(a.utilisation*100)}% utilisation: {eff:.3f} per 1M tokens")
    if "monthly_api_cost" in out:
        print(f"Monthly volume {float(a.monthly_tokens):,.0f} tokens: self-hosted {out['monthly_self_hosted_cost']:,.2f} "
              f"({out['instances_needed']} instance(s)) vs managed API {out['monthly_api_cost']:,.2f}")
        print(f"Break-even for one instance: about {out['break_even_monthly_tokens']:,} tokens a month. Cheaper: {out['cheaper']}")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("memory", help="weights + KV cache sizing")
    m.add_argument("--preset", choices=sorted(PRESETS))
    m.add_argument("--config", help="path to a Hugging Face config.json")
    m.add_argument("--params", type=float, help="total parameters in billions")
    m.add_argument("--layers", type=int)
    m.add_argument("--kv-heads", type=int)
    m.add_argument("--head-dim", type=int)
    m.add_argument("--weights", choices=sorted(BYTES), default="fp16")
    m.add_argument("--kv", choices=sorted(BYTES), default="fp16")
    m.add_argument("--context", default="8k", help="max context per request, e.g. 8k, 32k, 128000")
    m.add_argument("--concurrency", type=int, default=32, help="peak requests in flight")
    m.add_argument("--overhead", type=float, default=0.10, help="runtime overhead fraction (activations, CUDA graphs)")
    m.add_argument("--format", choices=["table", "json"], default="table")
    m.set_defaults(fn=memory)
    c = sub.add_parser("cost", help="cost per 1M tokens and managed API comparison")
    c.add_argument("--price", type=float, required=True, help="hourly price of the server or instance")
    c.add_argument("--tps", type=float, required=True, help="tokens per second measured while meeting the SLO")
    c.add_argument("--utilisation", type=float, default=1.0, help="share of the day the box is busy, 0-1")
    c.add_argument("--monthly-tokens", help="expected tokens per month, e.g. 2e9")
    c.add_argument("--api-in", type=float, help="managed API price per 1M input tokens")
    c.add_argument("--api-out", type=float, help="managed API price per 1M output tokens")
    c.add_argument("--output-share", type=float, default=0.25, help="share of tokens that are output")
    c.add_argument("--format", choices=["table", "json"], default="table")
    c.set_defaults(fn=cost)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main(sys.argv[1:])

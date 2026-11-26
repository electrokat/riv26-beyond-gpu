# Reading Latitude.sh plan data

## Bare metal plans (`plans-list`, REST `GET /plans`)

Useful filters:

| Parameter | Example | Effect |
|---|---|---|
| filterGpu | true | Only GPU plans |
| filterInStock | true | Only plans with stock somewhere |
| filterLocation | SYD | Only plans offered at that site |
| filterStockLevel | high | unavailable, low, medium, high or unique |
| filterSlug | g4.b300.large | One plan |

Shape of one plan (trimmed):

```json
{
  "id": "plan_...",
  "attributes": {
    "slug": "g4.b300.large",
    "name": "g4.b300.large",
    "specs": {
      "cpu": { "type": "...", "cores": 0, "count": 2 },
      "memory": { "total": 0 },
      "gpu": { "type": "NVIDIA HGX B300", "count": 8, "vram_per_gpu": 288, "interconnect": "NVLink" },
      "drives": [ { "count": 2, "size": "...", "type": "NVME" } ],
      "nics": [ { "count": 2, "type": "100 Gbps" } ]
    },
    "regions": [
      {
        "name": "United States",
        "locations": { "available": ["DAL", "NYC"], "in_stock": ["DAL"] },
        "stock_level": "low",
        "deploys_instantly": ["DAL"],
        "pricing": { "USD": { "hour": 64.0, "month": 46720.0, "year": null } }
      }
    ]
  }
}
```

The values above are illustrative only. Always use the live response.

Interpretation:

- A site in `available` but not in `in_stock` means Latitude.sh offers the plan there but has no ready servers right now. Suggest checking back, a nearby site, or talking to sales for a reserved or Build option.
- `stock_level` applies to the whole region entry. Use `in_stock` for the per-site answer.
- `deploys_instantly` means the server is racked and ready, typically deployed in seconds.
- Prices are per server, not per GPU. Divide by `specs.gpu.count` for price per GPU hour.

## GPU VM plans (`plans-vm-list`, REST `GET /plans/virtual_machines`)

- Filter with `filterGpu: true`.
- `attributes.specs.gpu` is a string (GPU type) and `vram_per_gpu` is on `specs`. VMs may not report a GPU count, so treat it as one GPU unless the name or specs say otherwise.
- `regions[].locations.available` and `regions[].locations.in_stock` work the same way as bare metal.

## Locations (`regions-get`, REST `GET /regions`)

- Each entry has `slug` (site code used in plans), `name` (city), `country.name` and `network_group`.
- Use it to turn site codes into "Sydney, Australia" style names for the user.
- Current locations are also listed at https://www.latitude.sh/locations.

## Next steps to offer the user

- Deploy from the dashboard: https://www.latitude.sh/dashboard
- GPU overview: https://www.latitude.sh/accelerate
- Pricing: https://www.latitude.sh/pricing
- Custom or reserved GPU capacity: https://www.latitude.sh/build
- Private connectivity to AWS, GCP or Azure: https://www.latitude.sh/cloud-gateway

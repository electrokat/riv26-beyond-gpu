# AWS GPU capacity signals

AWS does not publish live On-Demand stock. The sweep script combines four signals so the user gets an honest picture.

## 1. Offered (DescribeInstanceTypeOfferings)

- Lists the Availability Zones in each region where an instance type is sold.
- It answers "can I launch this type here at all", not "is there capacity now".
- New GPU families roll out region by region (for example P6-B300 and G7e have been added to more regions through 2026), so always check live rather than relying on a static list.

## 2. Capacity Blocks for ML (DescribeCapacityBlockOfferings)

- Real reservable capacity for a fixed duration, starting from about 30 minutes out to 8 weeks ahead.
- Returns offers with Availability Zone, start and end dates, and an upfront fee. Divide the fee by hours and instance count for an effective hourly rate.
- Supported only for selected types and regions (P4d, P4de, P5, P5e, P5en, P6-B200, P6-B300, Trn1, Trn2, and P6e-GB200 and Trn2 UltraServers). The script only calls it for those families and records any errors.
- Limits: up to 64 instances per block and 256 across all blocks per account. Blocks end at 11:30 UTC.
- No offers in the window means no reservable capacity for that size and duration. Try a longer window, a different duration or fewer instances.

## 3. Spot placement score (GetSpotPlacementScores)

- A score from 1 to 10 per region for the target number of instances. Higher means AWS is more likely to fulfil a Spot request, which is a useful proxy for spare capacity.
- Not every GPU type supports Spot. Types sold only through Capacity Blocks may return errors or no score.

## 4. Service quotas (optional)

- "Running On-Demand P instances" and "Running On-Demand G and VT instances" are vCPU quotas per region.
- An 8 GPU P-family instance needs 192 vCPUs of P quota. Many accounts start at 0 for P instances and need a quota increase before any launch.

## Pricing

- The script reads Linux, shared tenancy, On-Demand list prices from the AWS Price List API (queried in us-east-1, filtered by `regionCode`).
- Savings Plans, Reserved Instances, Spot and Capacity Block prices differ. Label which price you are showing.

## Read-only IAM policy for the AWS profile

Give the AWS profile used by the AWS MCP Server (or the local script) only these permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "GpuFinderReadOnly",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeRegions",
        "ec2:DescribeInstanceTypes",
        "ec2:DescribeInstanceTypeOfferings",
        "ec2:DescribeCapacityBlockOfferings",
        "ec2:GetSpotPlacementScores",
        "pricing:GetProducts",
        "servicequotas:ListServiceQuotas"
      ],
      "Resource": "*"
    }
  ]
}
```

This keeps searching strictly read only on the AWS side, even though the AWS MCP Server itself can run any API the profile allows. Deploying needs the separate policy in the deploy-gpu skill (references/aws-deploy-policy.md).

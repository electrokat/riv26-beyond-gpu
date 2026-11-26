# AWS IAM policy for deploying with Global GPU Finder

Use this in addition to the read-only finder policy, ideally on a separate AWS profile so searching and deploying are clearly separated. Terminating is limited to instances this power tagged.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DescribeForDeploy",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstanceTypes",
        "ec2:DescribeInstanceTypeOfferings",
        "ec2:DescribeInstances",
        "ec2:DescribeImages",
        "ec2:DescribeVpcs",
        "ec2:DescribeSubnets",
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeKeyPairs",
        "ec2:DescribeCapacityBlockOfferings",
        "ec2:DescribeCapacityReservations",
        "servicequotas:ListServiceQuotas"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ReadDeepLearningAmiParameter",
      "Effect": "Allow",
      "Action": "ssm:GetParameter",
      "Resource": "arn:aws:ssm:*::parameter/aws/service/deeplearning/*"
    },
    {
      "Sid": "LaunchAndSetUp",
      "Effect": "Allow",
      "Action": [
        "ec2:RunInstances",
        "ec2:CreateSecurityGroup",
        "ec2:AuthorizeSecurityGroupIngress",
        "ec2:ImportKeyPair",
        "ec2:PurchaseCapacityBlock"
      ],
      "Resource": "*"
    },
    {
      "Sid": "TagOnCreateOnly",
      "Effect": "Allow",
      "Action": "ec2:CreateTags",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "ec2:CreateAction": [
            "RunInstances",
            "CreateSecurityGroup",
            "ImportKeyPair",
            "PurchaseCapacityBlock"
          ]
        }
      }
    },
    {
      "Sid": "TerminateOnlyOwnInstances",
      "Effect": "Allow",
      "Action": "ec2:TerminateInstances",
      "Resource": "arn:aws:ec2:*:*:instance/*",
      "Condition": {
        "StringEquals": { "aws:ResourceTag/created-by": "global-gpu-finder" }
      }
    }
  ]
}
```

Notes:

- Remove `ec2:PurchaseCapacityBlock` if users should never be able to buy Capacity Blocks from Kiro.
- To limit which GPU types can be launched, add a condition on `ec2:InstanceType` to the `LaunchAndSetUp` statement, for example `"StringLike": {"ec2:InstanceType": ["g7e.*", "p6-b300.*"]}` (split RunInstances into its own statement first, since that condition key does not apply to the other actions).
- To limit regions, add `"StringEquals": {"aws:RequestedRegion": ["us-east-1", "ap-southeast-2"]}`.

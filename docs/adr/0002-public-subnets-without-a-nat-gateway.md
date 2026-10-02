# 2. Run tasks in public subnets without a NAT Gateway

- Status: accepted
- Date: 2026-09-29

## Context

The Fargate task needs outbound HTTPS to source APIs (Salesforce, NetSuite, USGS) and to AWS endpoints (ECR, CloudWatch Logs, Secrets Manager, SSM). The usual design puts tasks in private subnets behind a NAT Gateway. That costs about $33/month per gateway plus data processing, which is more than everything else in this stack combined, and it burns through Free plan credits quickly. Interface VPC endpoints don't remove the need for NAT, because the source APIs are on the public internet, and each endpoint adds about $7/month per AZ.

## Decision

- **Tasks:** run in public subnets with a public IP. Their security group has **no inbound rules** and allows outbound traffic only on 443 (anywhere) and 5432 (the database security group).
- **Database:** RDS runs in isolated subnets with no route out of the VPC, and accepts connections only from the task security group.
- **Network ACLs:** mirror the security groups as a second, stateless layer. The public subnets accept only return traffic on ephemeral ports. The isolated subnets accept only Postgres traffic from the public subnets.
- **Default security group:** emptied, so it allows nothing.
- **Flow logs:** VPC flow logs record rejected traffic for 14 days.

## Consequences

- **What the public IP exposes:** a public IP on a task that has no listening service and a security group with no inbound rules can't be reached. The exposure is the task's egress, which is limited to HTTPS and Postgres.
- **What we give up:** a fixed egress IP (some source APIs allowlist IPs), and the defence-in-depth of having no public address at all.
- **Moving to NAT later:** add a NAT Gateway and private subnets to `modules/network`, move the runner's `subnet_ids` to them, and set `assign_public_ip = false`. No other module changes.

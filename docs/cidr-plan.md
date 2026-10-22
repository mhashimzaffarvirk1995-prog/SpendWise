# VPC CIDR plan (used in Week 6)

## dev VPC: 10.0.0.0/16 (65,536 IPs), region ap-southeast-2, 2 AZs

| Subnet | CIDR | Range | AZ | Holds | Internet |
|---|---|---|---|---|---|
| public-a | 10.0.0.0/20 | 10.0.0.0 – 10.0.15.255 | 2a | ALB only | in + out (IGW) |
| public-b | 10.0.16.0/20 | 10.0.16.0 – 10.0.31.255 | 2b | ALB only | in + out (IGW) |
| app-a | 10.0.32.0/20 | 10.0.32.0 – 10.0.47.255 | 2a | 9 services (ECS/EKS) | out only (NAT / VPC endpoints) |
| app-b | 10.0.48.0/20 | 10.0.48.0 – 10.0.63.255 | 2b | 9 services (ECS/EKS) | out only |
| data-a | 10.0.64.0/20 | 10.0.64.0 – 10.0.79.255 | 2a | RDS Postgres, Redis | none |
| data-b | 10.0.80.0/20 | 10.0.80.0 – 10.0.95.255 | 2b | RDS Postgres, Redis | none |
| spare | 10.0.96.0/20 … 10.0.240.0/20 | 10 × /20 | – | future (extra AZ, EKS pods, endpoints) | – |

## Rules used
- `/16` → 16 × `/20` (each prefix bit halves the block). A `/20` has 4,096 IPs; its third octet steps by 256 − 240 = 16.
- AWS reserves 5 IPs per subnet: e.g. in public-a `10.0.0.0` (network), `.1` (VPC router), `.2` (DNS), `.3` (reserved), `10.0.15.255` (broadcast) → 4,091 usable.
- `/20` instead of `/24`: on EKS every pod takes a VPC IP; small subnets run out fast.
- Two AZs per tier so one data-center failure does not take the app down.
- No overlap with the workbench VPC `172.31.0.0/16` (required for peering/VPN). prod will use `10.1.0.0/16` for the same reason.

## Verify

python3 -c “import ipaddress as i; print(*[s for s in i.ip_network(‘10.0.0.0/16’).subnets(new_prefix=20)][:6], sep='\n')”


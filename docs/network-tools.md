# Week 2 — Network tools: one question, one answer each

Domain: `52-65-48-147.sslip.io` → Elastic IP `52.65.48.147` (ap-southeast-2).
HTTPS tests run on the server use `--resolve DOMAIN:443:127.0.0.1` because the SG allows 443 only from my home IP.

## 1. `curl -v` — what happens inside one HTTPS request?

curl -sv --resolve $D:443:127.0.0.1 https://$D/login -o /dev/null

- `ALPN: curl offers h2,http/1.1` → `server accepted h2`: HTTP/2 is negotiated *inside* the TLS handshake, no extra round trip.
- `TLSv1.3 / TLS_AES_256_GCM_SHA384 / X25519 / id-ecPublicKey`: TLS 1.3 (1-RTT handshake), AES-256-GCM cipher, X25519 key exchange (forward secrecy), ECDSA certificate.
- `< strict-transport-security: max-age=86400`: HSTS is on.

## 2. `dig +trace` — how does the world find our domain?
| Step | Asked | Time | Answer |
|---|---|---|---|
| 1 | 127.0.0.53 (systemd-resolved) | 1 ms | root server list |
| 2 | c.root-servers.net | 1 ms | go to `.io` servers |
| 3 | a2.nic.io | 2 ms | go to `sslip.io` nameservers |
| 4 | ns-ovh.sslip.io | 321 ms | `A 52.65.48.147`, TTL 3600 |

Root and `.io` servers are anycast with a node in Sydney; sslip.io's nameserver is in France. TTL 3600 means the slow first lookup is cached for an hour.

## 3. `nslookup` / `resolvectl` — who answers DNS for the server?
`127.0.0.53` (systemd-resolved, a local cache) → forwards to `172.31.0.2`, the Amazon VPC resolver (VPC CIDR base + 2).

## 4. `traceroute -n 1.1.1.1` — how far is the internet?
| Hop | What |
|---|---|
| 1–2 `242.x`, `240.x` | AWS internal network (240.0.0.0/4 is never used on the public internet) |
| 3 `*` | router that does not answer ICMP — not a broken path |
| 4 `108.162.x` | Cloudflare |
| 5 `1.1.1.1` 0.9 ms | Cloudflare's Sydney node — 1.1.1.1 is anycast |

## 5. `openssl s_client -showcerts` — is the certificate chain valid?

0 CN=52-65-48-147.sslip.io (our cert)
1 Let’s Encrypt YE1 (intermediate, signed #0)
2 ISRG Root YE (new LE root)
3 ISRG Root X2 → X1 (cross-signs for older devices)

`Verify return code: 0 (ok)`, `TLSv1.3`, `TLS_AES_256_GCM_SHA384`. The 4-cert chain is the cost of compatibility with older trust stores.

## Bonus — `ss -ltnp` and `tcpdump`
- nginx listens on `0.0.0.0:80/443` (master + 2 workers = 2 vCPUs); the app listens only on `127.0.0.1:8000` and `127.0.0.1:5001–5008`.
- `tcpdump -i lo port 5001` during a wrong-password login showed web-app → auth-service `POST /login`, auth-service → `401 UNAUTHORIZED`, with the same `X-Request-ID` as nginx's `rid=`.
- Inside the box everything after nginx is plain HTTP — the password is readable in the capture. TLS terminates at nginx; mTLS / a service mesh would close that gap.

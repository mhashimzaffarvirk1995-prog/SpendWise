# Request flow (local workbench): adding one transaction

Traced live on 22 Oct 2026 with request ID `4ca1495b7ed4d3c8bdd79843f653bcab`
(nginx `rid=` → `journalctl -u spendwise | grep <rid>`).

Browser
│ DNS: 52-65-48-147.sslip.io → 52.65.48.147 (Elastic IP)
│ HTTPS :443 (TLS 1.3, HTTP/2, Let’s Encrypt cert)
▼
AWS Security Group ── 443 only from my ISP block 139.135.38.0/24 (+1 /32); 80 open for ACME + redirect
▼
ufw on the host ── allows 80/tcp, 443/tcp
▼
nginx :443 ── TLS TERMINATES HERE ──────────────────────────────────────────────
│ rate limit (perip 10r/s), request_id generated, headers added
│ HTTP/1.1 keepalive → 127.0.0.1:8000
▼
web-app :8000 (Flask, thread in run.py)
│ checks session cookie (signed with SECRET_KEY) + CSRF token
│ takes JWT from session[“access_token”]
│ HTTP → 127.0.0.1:5002 Authorization: Bearer <JWT>, X-Request-ID
▼
transaction-service :5002
│ verifies JWT signature locally (HS256, JWT_SECRET) — no call to auth-service
│ SQLAlchemy INSERT + db.session.commit()
▼
/opt/spendwise/instance → /var/lib/spendwise/instance/transactions.sqlite3 (LVM volume)


## Hop by hop

| # | From → To | Port / protocol | Encrypted? | Key headers / data | Who checks what |
|---|---|---|---|---|---|
| 1 | Browser → nginx | 443, HTTP/2 over TLS 1.3 | yes | `Cookie: session=…`, form with `csrf_token` | SG + ufw allow the packet |
| 2 | nginx → web-app | 8000, HTTP/1.1 | **no** (localhost) | `X-Request-ID`, `X-Forwarded-For`, `X-Forwarded-Proto: https`, `X-Real-IP`, `Host` | nginx: rate limit, body ≤ 5 MB, timeouts 5 s / 30 s |
| 3 | web-app → transaction-service | 5002, HTTP/1.1 | **no** (localhost) | `Authorization: Bearer <JWT>`, `X-Request-ID` | web-app: session + CSRF |
| 4 | transaction-service → SQLite | file I/O | no | INSERT row | service: JWT signature + expiry |
| 5 | Response back | — | — | 201 from service → web-app 302 (Post/Redirect/Get) → browser loads /dashboard | — |

Measured: transaction-service 9.07 ms, web-app total 11.63 ms.

## Where TLS terminates
At nginx. Everything after hop 1 is plain HTTP on 127.0.0.1. Anyone with root on the box can read it
(we saw a password in a `tcpdump -i lo` capture). Fixes later: mTLS / service mesh (Week 24), and keeping
services off shared hosts (containers in Week 3).

## Who trusts whom
- auth-service is only involved at **login**: it checks the password and issues the JWT.
- Every backend verifies the JWT itself with the shared `JWT_SECRET` → anyone holding that secret can mint tokens (see Week 1 `local-keys.json`).
- The JWT rides inside Flask's **client-side signed session cookie**: the browser cannot change it (signed with `SECRET_KEY`) but can base64-decode it. TODO: `SESSION_COOKIE_SECURE=true` now that the site is HTTPS-only.

## What breaks where (seen this week)
| Status | Hop | Meaning |
|---|---|---|
| timeout (no TCP) | SG | client IP not allowed (ISP NAT pool changed the source IP) |
| 429 | nginx | rate limit hit |
| 400 | web-app | missing/invalid CSRF token |
| 401 | backend | missing/invalid/expired JWT |
| 502 | nginx → web-app | app not listening (`111 Connection refused`) |
| 504 | nginx → web-app | app too slow (`110 timed out`, 30 s) |

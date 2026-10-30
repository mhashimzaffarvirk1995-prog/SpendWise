#!/usr/bin/env python3
"""End-to-end smoke test for the running Compose stack. Standard library only.

Run it on the stack's network:
  docker run --rm --network spendwise_backend -v "$PWD/scripts:/tools:ro" python:3.12-slim \
    python /tools/compose-smoke.py
Exit code 0 = every check passed.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

SERVICES = ["auth", "transaction", "budget", "notification", "reporting", "recurring", "savings", "import"]
URL = {name: f"http://{name}-service:8080" for name in SERVICES}
URL["web"] = "http://web-app:8080"
failures = 0


def call(method, url, body=None, token=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
            return response.status, response.headers, (json.loads(raw) if raw[:1] in (b"{", b"[") else {})
    except urllib.error.HTTPError as error:
        return error.code, error.headers, {}


def check(name, ok, detail=""):
    global failures
    failures += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  {name:36} {detail}")
    return ok


# 1) every service is ready (its own dependencies answer)
for name in SERVICES + ["web"]:
    status, _, _ = call("GET", URL[name] + "/ready")
    check(f"{name} /ready", status == 200, f"HTTP {status}")

# 2) a brand-new user can sign up and log in
email = f"smoke-{int(time.time())}@example.com"
status, _, _ = call("POST", URL["auth"] + "/signup",
                    {"name": "Smoke Test", "email": email, "password": "smoke-test-123"})
check("signup", status == 201, f"HTTP {status} {email}")
status, _, body = call("POST", URL["auth"] + "/login", {"email": email, "password": "smoke-test-123"})
token = body.get("access_token")
if not check("login returns a token", status == 200 and bool(token), f"HTTP {status}"):
    sys.exit(1)

month = datetime.now(timezone.utc).strftime("%Y-%m")
report_url = f"{URL['reporting']}/reports/monthly?month={month}"


def report():
    status, headers, body = call("GET", report_url, token=token)
    return status, headers.get("X-Cache"), body.get("summary", {}).get("expenses"), body.get("budgets") or []


# 3) report cache: MISS, then HIT
s, cache, expenses, _ = report()
check("report #1 is a MISS", s == 200 and cache == "MISS", f"HTTP {s} {cache} expenses={expenses}")
s, cache, expenses, _ = report()
check("report #2 is a HIT", s == 200 and cache == "HIT", f"HTTP {s} {cache} expenses={expenses}")

# 4) a new transaction makes the next report fresh at once
status, _, _ = call("POST", URL["transaction"] + "/transactions",
                    {"title": "smoke", "amount": 123, "type": "expense", "category": "Food"}, token)
check("create transaction", status == 201, f"HTTP {status}")
s, cache, expenses, _ = report()
check("report after transaction is fresh", s == 200 and cache == "MISS" and expenses == 123.0,
      f"{cache} expenses={expenses}")

# 5) a new budget does the same
status, _, _ = call("POST", URL["budget"] + "/budgets", {"month": month, "category": "Food", "amount": 1000}, token)
check("create budget", status == 201, f"HTTP {status}")
s, cache, _, budgets = report()
check("report after budget is fresh", s == 200 and cache == "MISS" and len(budgets) == 1,
      f"{cache} budgets={len(budgets)}")

print(f"\nSMOKE: {'PASS' if failures == 0 else 'FAIL'} ({failures} failed)")
sys.exit(1 if failures else 0)

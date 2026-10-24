#!/usr/bin/env python3
"""Turn SpendWise JSON log lines (stdin) into a readable table.
Usage: docker logs web-app 2>&1 | scripts/log-table.py
       journalctl -u spendwise -o cat | grep <request_id> | scripts/log-table.py
"""
import json
import sys

for line in sys.stdin:
    try:
        d = json.loads(line)
    except ValueError:
        continue
    t = d.get("timestamp", "")[11:23]
    print(f'{t}  {d.get("service", "?"):22} {d.get("method", ""):5} {d.get("path", ""):26} '
          f'{d.get("status_code", "")}  {d.get("duration_ms", "")} ms  '
          f'rid={d.get("request_id", "")[:8]}  {d.get("message", "")}')

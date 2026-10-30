#!/usr/bin/env python3
"""Print row counts for every table in every SQLite file under a folder (read-only).
Usage: python3 sqlite-counts.py /data
"""
import glob
import sqlite3
import sys

folder = sys.argv[1] if len(sys.argv) > 1 else "/data"
for path in sorted(glob.glob(f"{folder}/*.sqlite3")):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for t in tables:
        n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        print(f"{path:32} {t:24} {n:6}")
    con.close()

#!/usr/bin/env python3
"""Copy one service's SQLite data into its Postgres database.

Run INSIDE that service's image, so the app's own models define the target schema.
Usage: python /tools/migrate-sqlite-to-pg.py /src/<file>.sqlite3
Needs <SERVICE>_DATABASE_URL (postgres) and the app's normal env vars.
Safe to re-run: a table that already has rows in Postgres is skipped.
"""
import sys

from sqlalchemy import Integer, MetaData, create_engine, func, select, text

import app as service  # the service's app.py (PYTHONPATH=/app in our images)

src_path = sys.argv[1]
src = create_engine(f"sqlite:///file:{src_path}?mode=ro&uri=true")
src_meta = MetaData()
src_meta.reflect(src)

with service.app.app_context():
    service.db.create_all()
    dst = service.db.engine
    quote = dst.dialect.identifier_preparer.quote
    with src.connect() as s, dst.begin() as d:  # one transaction: all tables or nothing
        for table in service.db.metadata.sorted_tables:
            if table.name not in src_meta.tables:
                print(f"skip    {table.name:22} (not in sqlite file)")
                continue
            existing = d.execute(select(func.count()).select_from(table)).scalar()
            if existing:
                print(f"skip    {table.name:22} (already {existing} rows in postgres)")
                continue
            cols = [c.name for c in table.columns]
            rows = [dict(r._mapping) for r in s.execute(select(src_meta.tables[table.name]))]
            rows = [{c: r.get(c) for c in cols} for r in rows]
            if rows:
                d.execute(table.insert(), rows)
            pk = list(table.primary_key.columns)
            if len(pk) == 1 and pk[0].name == "id" and isinstance(pk[0].type, Integer):
                d.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {quote(table.name)}), 0) + 1, false)"))
            print(f"copied  {table.name:22} {len(rows):6} rows")

# Week 1 — Linux system administration

## Why is `instance/local-keys.json` mode 0600?

`run.py` generates two random secrets on first start and loads them as env vars:

| Key | Used for |
|---|---|
| `SECRET_KEY` | Signing Flask session cookies |
| `JWT_SECRET` | Signing/verifying JWTs between services |

**0600** means only the `spendwise` user can read or write it. No other user or process on the box can read the secrets.

**If it leaks:** anyone with the keys can forge session cookies and JWTs and act as any user (e.g. `user_id: 1`) without a password. Fix: rotate the keys (generate new ones, restart). All old sessions and tokens become invalid.

**If it is deleted:** `run.py` silently generates new keys on the next start. No data is lost (databases are separate), but every user is logged out because old cookies no longer verify. No error is logged, which makes this hard to debug.

**Findings from reading the code:**
- *Race window:* the file is written with default permissions (usually 0644) and only then `chmod 600`'d. Fixed at the service level with `UMask=0077` in the systemd unit.
- *Backups contain the keys:* backup archives are therefore `chmod 600`.
- *Scaling:* two servers would each generate different keys and break logins. The real fix is a shared secret store (AWS Secrets Manager / Vault — Week 12).

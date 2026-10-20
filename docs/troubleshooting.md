
## Week 1 · Drill 1 — Port already in use
- **Symptom:** whole app down; `systemctl status` shows restart loop (counter 8+); healthcheck 18 FAIL.
- **Evidence:** `journalctl -u spendwise` → `Address already in use`, `status=1/FAILURE`, `Scheduled restart job` every 5s.
- **Cause:** a stray `python3 -m http.server` (PID 1193) was listening on 127.0.0.1:5002. All 9 services run inside one `run.py` process, so one port clash kills everything.
- **Diagnose:** `sudo ss -ltnp 'sport = :5002'` → shows owning process + PID.
- **Fix:** `ps -o user,pid,cmd -p <PID>` to confirm it's safe → `kill <PID>`; systemd's next restart succeeded on its own.
- **Lesson:** single-process design = single point of failure (containers fix this in Week 3). Always check what a process is before killing it.

## Week 1 · Drill 2 — Data disk 100% full
- **Symptom:** app still `active`, all `/health` endpoints return 200, but new transactions fail to save (`database or disk is full`). Healthcheck only flagged disk %.
- **Diagnose:** `df -h /var/lib/spendwise` → 100%. `du -xh --max-depth=1` showed `instance/` only 144K vs 15G total → the space is in a loose file, not a folder. `find /var/lib/spendwise -xdev -type f -size +100M` → `junk.bin` 15G (root-owned).
- **Fix:** confirmed the file was junk → `rm`. Alternative when you can't delete yet: `lvextend -r -L +4G` from VG free space to buy time.
- **Lesson:** "healthy" HTTP checks can hide a broken write path. Added a real write test to `healthcheck.sh` (as the `spendwise` user).

## Week 1 · Drill 3 — local-keys.json deleted
- **Symptom:** after a restart every user is logged out. App `active`, healthcheck 0 failed, no errors in journald.
- **Diagnose:** `ls -l --time-style=full-iso instance/local-keys.json` → brand-new timestamp while DB files are old; `sha256sum` fingerprint changed → `run.py` silently generated new SECRET_KEY/JWT_SECRET, so old cookies/JWTs no longer verify. New file was created 0600 thanks to `UMask=0077`.
- **Fix:** extracted `local-keys.json` from the latest nightly backup, verified the fingerprint matched, `install -o spendwise -g spendwise -m 600`, restarted.
- **Lesson:** silent failures are the worst kind; health checks can't see them. Secrets must be backed up (or live in a secret store) and backups must be tested by actually restoring.

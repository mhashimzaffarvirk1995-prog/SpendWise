
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

## Week 1 · Drill 4 — kill -9 a single service
- **Finding:** `ss -ltnp` shows all 9 ports owned by one PID; `ps -T -p <PID>` shows 11 threads. Services are threads inside `run.py`, so one service cannot be killed alone — `kill -9` takes down the whole app (systemd restarts it in 5s, see Step 5).
- **Lesson:** no fault isolation in the current design. Revisit in Week 3 when each service gets its own container.

## Week 1 · Drill 5 — Wrong file owner
- **Symptom:** app `active`, healthcheck 0 failed, but budgets cannot be saved: `attempt to write a readonly database`.
- **Diagnose:** `sudo find /var/lib/spendwise ! -user spendwise` → `budgets.sqlite3` owned by `root:root`.
- **Fix:** `chown -R spendwise:spendwise /var/lib/spendwise`. Also found every DB was 0644 (world-readable, created before `UMask=0077`) → `chmod 600`.
- **Lesson:** a directory write test doesn't prove existing files are writable. Added ownership + world-readable checks to `healthcheck.sh`. When restoring files, use `install -o -g -m`, not `cp`.

## Week 1 · Drill 6A — Typo in /etc/fstab (with nofail)
- **Symptom:** after reboot the server is reachable but the app is down. `systemctl is-active spendwise` → `inactive`; `systemctl --failed` is empty (nothing "failed", things are *waiting*).
- **Diagnose:** `df -h /var/lib/spendwise` → `/dev/root`, i.e. the data disk is not mounted. `journalctl -b` → `Expecting device ...5c347350...`. `systemctl list-jobs` shows the start job waiting. `lsblk -f` vs `/etc/fstab` → one character different in the UUID.
- **Fix:** restored `/etc/fstab.bak`, `findmnt --verify`, `daemon-reload`, `mount -a`, started the service.
- **What saved us:** `nofail` let the box boot (SSM still reachable). `RequiresMountsFor=/var/lib/spendwise` stopped the app from starting on the empty root-disk folder and creating fresh empty databases there.
- **Prevention:** after any fstab edit, run `findmnt --verify` and `mount -a` *before* rebooting.

## Week 1 · Drill 7 — Memory leak / OOM-killer
- **Setup:** a leaking process (`+10 MB / 0.3 s`) run via `systemd-run --property=MemoryMax=200M`.
- **Symptom:** `systemctl status leaky` → `Failed with result 'oom-kill'`; last log line `190 MB`.
- **Diagnose:** `journalctl -k | grep -i oom` → `constraint=CONSTRAINT_MEMCG ... oom_memcg=/system.slice/leaky.service ... Killed process 1382`. The kill was limited to that unit's cgroup; `spendwise` stayed active.
- **Gap found:** `spendwise.service` had no memory limit. A real leak would eat all 7.6 GB, trigger the *global* OOM-killer, and could take out unrelated processes (SSM agent, journald).
- **Fix:** added `MemoryHigh=512M` (throttle + reclaim) and `MemoryMax=768M` (cgroup OOM → only the app dies → `Restart=on-failure` brings it back).
- **Lesson:** every long-running service gets a memory ceiling; cgroups turn "the server died" into "the app restarted".

## Week 1 · Gotcha — `sudo chmod` with a glob says "No such file"
- **Symptom:** `sudo chmod 600 /var/lib/spendwise/instance/*.sqlite3` → `cannot access '...*.sqlite3'`, yet the files exist. Healthcheck kept showing 7 world-readable warnings.
- **Cause:** the `*` is expanded by *my* shell (user `ubuntu`) before sudo runs. `/var/lib/spendwise` is `750 spendwise`, so `ubuntu` can't list it → the glob stays literal.
- **Fix:** `sudo sh -c 'chmod 600 /var/lib/spendwise/instance/*.sqlite3'` so root's shell expands it.
- **Lesson:** sudo elevates the command, not your shell. The healthcheck permission check caught a fix that silently never happened.

## Week 1 · Drill 8 — Crash loops and Restart= semantics
- **Test A:** two identical transient units, one `exit 0`, one `exit 1`, both `Restart=on-failure`. The `exit 0` unit ran once and stayed down; the `exit 1` unit kept restarting. → `on-failure` ignores clean exits, so an app that exits 0 unexpectedly stays down silently.
- **Test B:** `Restart=always`, `RestartSec=1`, instant crash → 5 restarts in <10s → `Start request repeated too quickly` → `failed`.
- **Gap found:** spendwise had `RestartSec=5` with default `StartLimitBurst=5` / `StartLimitIntervalSec=10s`. At most 2 starts fit in 10s, so the limit never trips → infinite restart loop (seen in Drill 1, counter 8+), log spam, and never a clear `failed` state.
- **Fix:** `Restart=always`; in `[Unit]`: `StartLimitIntervalSec=300`, `StartLimitBurst=10`. Verified: with port 5002 occupied the unit gave up after ~10 tries with `start-limit-hit`. Recovery needs `systemctl reset-failed` + `start`.
- **Lesson:** `StartLimit*` belongs in `[Unit]` (ignored in `[Service]`). Tune it against `RestartSec`, or it never triggers.

## Week 1 · Drill 9 — Cron job fails silently
- **Symptom:** `* * * * * root healthcheck.sh` in `/etc/cron.d/` — no output file, no error anywhere. Without `sudo`, `journalctl -u cron` even showed "No entries".
- **Diagnose:** `sudo journalctl -u cron` → `(root) CMD (healthcheck.sh)` (cron *did* run it) and `No MTA installed, discarding output` (the error was meant to be emailed; no mail system, so it was thrown away). Reproduced with `sudo env -i PATH=/usr/bin:/bin sh -c 'healthcheck.sh'` → `not found`, exit 127.
- **Cause:** relative command (not on cron's minimal PATH) + no output redirection.
- **Fix:** installed script to `/usr/local/bin/spendwise-healthcheck`; one `/etc/cron.d/spendwise` with `PATH=`, `MAILTO=""`, absolute paths, `>> log 2>&1` and a `date -Is` stamp; added the log to logrotate.
- **Gotchas:** use `sudo journalctl` when hunting; `%` in crontab means newline (escape as `\%`); test with `env -i` to mimic cron.

## Week 1 · Drill 10 — chmod -R 777
- **Symptom:** none. App `active`, `/login` 200, healthcheck `0 failed` (only WARNs). That's the danger: 777 never breaks anything, so it lives for months.
- **Impact proven:** as user `nobody`: read `local-keys.json` (164 bytes), created `evil.txt` in `instance/`, opened `auth.sqlite3`.
- **Fix:** removed unknown files; `find -type d -exec chmod 750`, `find -type f -exec chmod 600`, `instance/` 700 (never `chmod -R 600` — directories need `x`). Verified `nobody` gets `Permission denied`.
- **Secret rotation:** keys were readable by anyone, so treat them as leaked → stopped app, removed keys, started (new keys generated, 0600 via `UMask`), fingerprint changed. All sessions invalidated (acceptable). Took a fresh backup; older backups contain the burned keys.
- **Healthcheck:** world-writable files and `local-keys.json` ≠ 600 are now FAIL, not WARN.
- **Lesson:** "Permission denied" is a signal to find the right owner, never a reason for 777.

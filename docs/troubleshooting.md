
## Week 1 · Drill 1 — Port already in use
- **Symptom:** whole app down; `systemctl status` shows restart loop (counter 8+); healthcheck 18 FAIL.
- **Evidence:** `journalctl -u spendwise` → `Address already in use`, `status=1/FAILURE`, `Scheduled restart job` every 5s.
- **Cause:** a stray `python3 -m http.server` (PID 1193) was listening on 127.0.0.1:5002. All 9 services run inside one `run.py` process, so one port clash kills everything.
- **Diagnose:** `sudo ss -ltnp 'sport = :5002'` → shows owning process + PID.
- **Fix:** `ps -o user,pid,cmd -p <PID>` to confirm it's safe → `kill <PID>`; systemd's next restart succeeded on its own.
- **Lesson:** single-process design = single point of failure (containers fix this in Week 3). Always check what a process is before killing it.

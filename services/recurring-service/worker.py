"""Recurring-transaction worker as its own process: `python worker.py`.

Health: touches /tmp/worker-heartbeat every 15 s while the worker thread is alive.
If the thread dies, the process exits 1 so the container restart policy brings it back.
"""
import pathlib
import signal
import sys
import threading

import app as service

HEARTBEAT = pathlib.Path("/tmp/worker-heartbeat")

service.initialize_database()

stop = threading.Event()
for sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(sig, lambda *_: stop.set())

thread = service.start_background_worker(stop)
HEARTBEAT.touch()
print("recurring worker started", flush=True)

while not stop.wait(15):
    if thread.is_alive():
        HEARTBEAT.touch()
    else:
        print("recurring worker thread died; exiting for restart", flush=True)
        sys.exit(1)

thread.join(timeout=10)
print("recurring worker stopped", flush=True)

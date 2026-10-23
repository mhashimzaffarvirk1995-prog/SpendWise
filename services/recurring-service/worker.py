"""Recurring-transaction worker as its own process: `python worker.py`."""
import signal
import threading

import app as service

service.initialize_database()

stop = threading.Event()
for sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(sig, lambda *_: stop.set())

thread = service.start_background_worker(stop)
print("recurring worker started", flush=True)
stop.wait()
if thread is not None:
    thread.join(timeout=10)
print("recurring worker stopped", flush=True)

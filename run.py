"""Start SpendWise locally with persistent SQLite databases."""
import importlib.util
import json
import os
from pathlib import Path
import secrets
import signal
import sys
import threading

from dotenv import load_dotenv
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "services"))


def main():
    load_dotenv(ROOT / ".env")
    data = ROOT / "instance"
    data.mkdir(exist_ok=True)
    keys_path = data / "local-keys.json"
    if not keys_path.exists():
        keys_path.write_text(json.dumps({
            "SECRET_KEY": secrets.token_hex(32),
            "JWT_SECRET": secrets.token_hex(32),
        }))
        keys_path.chmod(0o600)
    for key, value in json.loads(keys_path.read_text()).items():
        os.environ.setdefault(key, value)
    os.environ.setdefault("APP_ENV", "development")
    os.environ.setdefault("SESSION_COOKIE_SECURE", "false")
    for key, filename in [("AUTH_DATABASE_URL", "auth.sqlite3"),
                          ("TRANSACTION_DATABASE_URL", "transactions.sqlite3"),
                          ("BUDGET_DATABASE_URL", "budgets.sqlite3"),
                          ("NOTIFICATION_DATABASE_URL", "notifications.sqlite3"),
                          ("RECURRING_DATABASE_URL", "recurring.sqlite3"),
                          ("SAVINGS_DATABASE_URL", "savings.sqlite3"),
                          ("IMPORT_DATABASE_URL", "imports.sqlite3")]:
        os.environ.setdefault(key, "sqlite:///" + str(data / filename))
    ports = {
        "auth-service": int(os.getenv("AUTH_SERVICE_PORT", "5001")),
        "transaction-service": int(os.getenv("TRANSACTION_SERVICE_PORT", "5002")),
        "budget-service": int(os.getenv("BUDGET_SERVICE_PORT", "5003")),
        "notification-service": int(os.getenv("NOTIFICATION_SERVICE_PORT", "5004")),
        "reporting-service": int(os.getenv("REPORTING_SERVICE_PORT", "5005")),
        "recurring-service": int(os.getenv("RECURRING_SERVICE_PORT", "5006")),
        "savings-service": int(os.getenv("SAVINGS_SERVICE_PORT", "5007")),
        "import-service": int(os.getenv("IMPORT_SERVICE_PORT", "5008")),
        "web-app": int(os.getenv("WEB_APP_PORT", "8000")),
    }
    if len(set(ports.values())) != 9 or any(not 1 <= p <= 65535 for p in ports.values()):
        raise RuntimeError("Choose nine distinct ports between 1 and 65535.")
    os.environ.setdefault("AUTH_SERVICE_URL", f"http://127.0.0.1:{ports['auth-service']}")
    os.environ.setdefault("TRANSACTION_SERVICE_URL", f"http://127.0.0.1:{ports['transaction-service']}")
    os.environ.setdefault("BUDGET_SERVICE_URL", f"http://127.0.0.1:{ports['budget-service']}")
    os.environ.setdefault("NOTIFICATION_SERVICE_URL", f"http://127.0.0.1:{ports['notification-service']}")
    os.environ.setdefault("REPORTING_SERVICE_URL", f"http://127.0.0.1:{ports['reporting-service']}")
    for name in ("recurring", "savings", "import"):
        os.environ.setdefault(name.upper() + "_SERVICE_URL", f"http://127.0.0.1:{ports[name + '-service']}")
    servers = []
    modules = []
    workers = []
    started = []
    stopped = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    try:
        for name, port in ports.items():
            spec = importlib.util.spec_from_file_location(name, ROOT / "services" / name / "app.py")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            if hasattr(module, "initialize_database"):
                module.initialize_database()
            modules.append(module)
            servers.append(make_server("127.0.0.1", port, module.app, threaded=True))
        for server in servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            started.append(server)
        for module in modules:
            if hasattr(module, "start_background_worker"):
                workers.append(module.start_background_worker(stopped))
        print(f"SpendWise running at http://localhost:{ports['web-app']} (Ctrl+C to stop)", flush=True)
        stopped.wait()
    finally:
        stopped.set()
        for worker in workers:
            worker.join(timeout=10)
        for server in servers:
            if server in started:
                server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()

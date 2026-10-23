"""WSGI entrypoint for this service: `gunicorn --preload wsgi:app`."""
import app as service

# Create tables once. With --preload this runs in the gunicorn master
# before workers fork, so workers never race each other.
if hasattr(service, "initialize_database"):
    service.initialize_database()

app = service.app

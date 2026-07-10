"""WSGI entrypoint: `flask --app wsgi run` or gunicorn `wsgi:app`."""
from app import create_app

app = create_app()

"""Celery worker entrypoint: `celery -A celery_worker.celery worker`."""
from app import create_app
from app.core.celery_app import celery

app = create_app()

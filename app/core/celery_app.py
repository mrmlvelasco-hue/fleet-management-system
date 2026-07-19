"""Celery factory. Wired now (empty queue); Notification Engine uses it later.

IMPORTANT (root cause of the "page just keeps loading" bug on MO submit /
Send Test Email): Celery's defaults make task.delay() RETRY the initial
broker connection up to broker_connection_max_retries times (100 by
default) with growing backoff BEFORE raising an exception -- and that
retry loop runs synchronously in the calling thread. If Redis isn't
running (typical on a dev laptop with no worker started), every .delay()
call in a Flask request blocks for a long time instead of failing fast.

The settings below make an unreachable broker fail in ~2-3 seconds instead
of ~minutes, so calling code's except-and-fallback-to-synchronous logic
(see notification_engine._queue_email, routes.email_config_send_test)
actually gets a chance to run.
"""
from celery import Celery

celery = Celery("fms")


def init_celery(app):
    """Bind Celery config to the Flask app and run tasks in app context."""
    celery.conf.broker_url = app.config["CELERY_BROKER_URL"]
    celery.conf.result_backend = app.config["CELERY_RESULT_BACKEND"]

    # Fail fast instead of retrying an unreachable broker ~100 times.
    celery.conf.broker_connection_retry = False
    celery.conf.broker_connection_max_retries = 0
    celery.conf.broker_transport_options = {
        "max_retries": 0,
        "socket_connect_timeout": 3,
        "socket_timeout": 3,
    }
    # Result backend (also Redis) should fail fast the same way. Celery 5.3+
    # has its own separate retry policy for the result backend
    # (result_backend_always_retry / result_backend_max_retries) that is
    # NOT covered by broker_connection_retry above -- this was the
    # remaining source of a ~19s hang even after the broker settings.
    celery.conf.result_backend_transport_options = {
        "socket_connect_timeout": 3,
        "socket_timeout": 3,
    }
    celery.conf.result_backend_always_retry = False
    celery.conf.result_backend_max_retries = 0

    # None of this app's tasks are awaited for a return value (they're all
    # fire-and-forget notification/email sends) -- ignoring results means
    # .delay() never has to talk to the result backend at all, which is
    # both faster in the healthy case and removes the backend as a hang
    # source entirely when Redis is down.
    celery.conf.task_ignore_result = True

    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return celery

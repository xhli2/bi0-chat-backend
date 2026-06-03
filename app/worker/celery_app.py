from celery import Celery
from celery.signals import worker_process_init
from kombu import Queue

from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging()


@worker_process_init.connect
def _configure_worker_process_logging(**_: object) -> None:
    configure_logging()

celery_app = Celery(
    "backend_temp",
    broker=settings.effective_celery_broker_url,
    backend=settings.effective_celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_default_queue="default",
    task_default_exchange="default",
    task_default_routing_key="default",
    task_routes={
        "agent.run": {"queue": "default", "routing_key": "default"},
        "task.demo": {"queue": "default", "routing_key": "default"},
        "session.refresh_memory": {"queue": "default", "routing_key": "default"},
        "spliceai.run": {"queue": "default", "routing_key": "default"},
        "approval.scan_overdue": {"queue": "default", "routing_key": "default"},
    },
    task_queues=(
        Queue("high", routing_key="high"),
        Queue("default", routing_key="default"),
        Queue("low", routing_key="low"),
    ),
    beat_schedule={
        "scan-overdue-approvals": {
            "task": "approval.scan_overdue",
            "schedule": max(10, settings.approval_ticket_scan_interval_seconds),
        }
    },
)

if settings.environment == "test":
    celery_app.conf.update(
        task_always_eager=True,
        task_eager_propagates=True,
        task_ignore_result=True,
        result_backend=None,
    )

celery_app.autodiscover_tasks(["app.worker"])

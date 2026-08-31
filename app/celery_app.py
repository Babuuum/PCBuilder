from celery import Celery
from celery.signals import after_setup_logger, after_setup_task_logger

from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()

celery_app = Celery(
    "pcbuilder",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks"],
)
celery_app.conf.update(
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    result_serializer="json",
    task_serializer="json",
    timezone="Europe/Moscow",
    enable_utc=True,
    result_expires=3600,
    task_track_started=True,
    worker_concurrency=5,
    worker_prefetch_multiplier=1,
)


@after_setup_logger.connect
@after_setup_task_logger.connect
def setup_celery_logging(**_: object) -> None:
    configure_logging(settings.log_level)

from celery import Celery

from .config import settings

celery = Celery("cronsentinel", broker=settings.redis_url, backend=None, include=["cronsentinel.alerting.tasks", "cronsentinel.outbound.deliver"])
celery.conf.update(task_acks_late=True, worker_prefetch_multiplier=1, task_default_queue="notify",
                   task_time_limit=60, broker_connection_retry_on_startup=True)

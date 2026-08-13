import logging
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from redis import Redis
from rq import Queue

from app.core.config import get_settings
from app.storage.repository import Repository
from app.workers.jobs import execute_run

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def enqueue_scheduled_run() -> None:
    settings = get_settings()
    repo = Repository(settings)
    competitors = [item.strip() for item in settings.schedule_competitors.split(",") if item.strip()]
    payload = {"competitors": competitors}
    run_id = repo.create_pending_run(settings.schedule_workflow)
    queue = Queue(settings.queue_name, connection=Redis.from_url(settings.redis_url))
    queue.enqueue(execute_run, run_id, settings.schedule_workflow, payload, job_id=run_id)
    logger.info("scheduled_run_enqueued run_id=%s workflow=%s", run_id, settings.schedule_workflow)


def main() -> None:
    settings = get_settings()
    Repository(settings).init_db()
    if not settings.schedule_enabled:
        logger.info("scheduler_disabled")
        return
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(enqueue_scheduled_run, "interval", minutes=max(1, settings.schedule_interval_minutes), id="automation-schedule", replace_existing=True, next_run_time=datetime.now(timezone.utc))
    logger.info("scheduler_started interval_minutes=%s workflow=%s", settings.schedule_interval_minutes, settings.schedule_workflow)
    scheduler.start()


if __name__ == "__main__":
    main()

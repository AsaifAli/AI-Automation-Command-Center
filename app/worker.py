import logging

from redis import Redis
from rq import Queue, Worker

from app.core.config import get_settings
from app.core.telemetry import configure_telemetry
from app.storage.repository import Repository

logging.basicConfig(level=logging.INFO)


def main() -> None:
    settings = get_settings()
    Repository(settings).init_db()
    configure_telemetry(settings)
    connection = Redis.from_url(settings.redis_url)
    worker = Worker([Queue(settings.queue_name, connection=connection)], connection=connection)
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()

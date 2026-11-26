from __future__ import annotations

import logging
import multiprocessing
import os
import threading
import uuid

from redis import Redis
from rq import Queue, SimpleWorker
from rq.registry import StartedJobRegistry

from app.core.config import Settings
from app.core.telemetry import configure_telemetry

logger = logging.getLogger(__name__)


def _run_worker(settings: Settings, worker_name: str) -> None:
    """Run one non-forking RQ worker in a child process of the free API service."""
    logging.basicConfig(level=settings.log_level)
    # The worker is forked before the API process installs its own OTel
    # exporter threads. Configure a process-local provider after the fork.
    configure_telemetry(settings)
    worker_ttl = max(300, settings.workflow_job_timeout_seconds + 120)
    connection = Redis.from_url(
        settings.redis_url,
        decode_responses=False,
        socket_connect_timeout=5,
        # RQ blocks for worker_ttl - 15 seconds while waiting for work.
        # Keep the read timeout longer than that blocking wait so an idle
        # worker does not mistake normal BRPOP waiting for a Redis failure.
        socket_timeout=worker_ttl + 30,
        health_check_interval=30,
    )
    queue = Queue(settings.queue_name, connection=connection)

    worker = SimpleWorker(
        [queue],
        connection=connection,
        name=worker_name,
        worker_ttl=worker_ttl,
        job_monitoring_interval=30,
        maintenance_interval=300,
    )
    try:
        logger.info(
            "embedded_rq_worker_started worker=%s queue=%s",
            worker_name,
            settings.queue_name,
        )
        worker.work(with_scheduler=False, logging_level=settings.log_level)
        if not worker._stop_requested:
            logger.warning(
                "embedded_rq_worker_loop_exited worker=%s; supervisor will restart with a fresh identity",
                worker_name,
            )
    except Exception:
        logger.exception("embedded_rq_worker_loop_failed worker=%s", worker_name)
        # Exit this child instead of reusing a possibly stale RQ identity.
        # The parent supervisor starts a new child with a new worker name.
        raise



class EmbeddedRQWorker:
    """Supervise an RQ worker child process inside the existing API instance.

    The child uses SimpleWorker to avoid creating another per-job process. A
    Stop request terminates this child only, marks the RQ job canceled, and
    starts a fresh worker. The FastAPI parent stays available throughout.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.worker_name: str | None = None
        self._context = multiprocessing.get_context("fork")
        self._process: multiprocessing.Process | None = None
        self._lock = threading.RLock()
        self._shutdown = threading.Event()
        self._watcher: threading.Thread | None = None

    def _start_locked(self) -> None:
        if self._shutdown.is_set():
            return
        if self._process is not None and self._process.is_alive():
            return
        # Docker commonly runs the API as PID 1. PID-only RQ names collide
        # across overlapping Render deploys and after a hard-stopped child.
        # Give every child process its own identity so stale registrations do
        # not block the replacement worker from consuming queued jobs.
        worker_name = f"aiac-worker-{os.getpid()}-{uuid.uuid4().hex[:12]}"
        process = self._context.Process(
            target=_run_worker,
            args=(self.settings, worker_name),
            name=worker_name,
            daemon=True,
        )
        self.worker_name = worker_name
        process.start()
        self._process = process
        logger.info("embedded_rq_worker_process_started pid=%s", getattr(process, "pid", None))

    def start(self) -> None:
        with self._lock:
            self._start_locked()
            if self._watcher is None or not self._watcher.is_alive():
                self._watcher = threading.Thread(
                    target=self._watch,
                    name="embedded-rq-worker-supervisor",
                    daemon=True,
                )
                self._watcher.start()

    def _watch(self) -> None:
        backoff = 2
        while not self._shutdown.wait(5):
            with self._lock:
                process = self._process
                if self._shutdown.is_set():
                    return
                if process is None:
                    try:
                        self._start_locked()
                        backoff = 2
                    except Exception:
                        logger.exception("embedded_rq_worker_restart_failed")
                        backoff = min(backoff * 2, 30)
                    continue
                if process.is_alive():
                    backoff = 2
                    continue
                exitcode = getattr(process, "exitcode", None)
                try:
                    process.join(timeout=0)
                    close = getattr(process, "close", None)
                    if close:
                        close()
                except (AssertionError, ValueError):
                    logger.debug("embedded_rq_worker_process_reap_skipped")
                self._process = None
                logger.warning(
                    "embedded_rq_worker_process_not_running exitcode=%s; restarting",
                    exitcode,
                )
                try:
                    self._start_locked()
                except Exception:
                    logger.exception("embedded_rq_worker_restart_failed")
                    backoff = min(backoff * 2, 30)
            self._shutdown.wait(backoff)

    def stop_current_job(self, job) -> bool:
        """Hard-stop the embedded worker process only when it owns this job."""
        if getattr(job, "worker_name", None) != self.worker_name:
            return False
        with self._lock:
            process = self._process
            if process is None:
                return False
            if process.is_alive():
                process.terminate()
            process.join(timeout=8)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
            if process.is_alive():
                logger.error(
                    "embedded_rq_worker_process_stop_unconfirmed pid=%s",
                    getattr(process, "pid", None),
                )
                return False
            self._process = None
            close = getattr(process, "close", None)
            if close:
                close()
            cancelled = False
            try:
                # RQ 2.x stores active execution IDs in StartedJobRegistry.
                # Job.cancel() changes status before its generic registry cleanup,
                # so explicitly remove the now-dead execution first.
                started_registry = StartedJobRegistry(
                    job.origin,
                    connection=job.connection,
                    job_class=job.__class__,
                    serializer=job.serializer,
                )
                started_registry.remove_executions(job)
                job.cancel()
                cancelled = True
            except Exception:
                logger.info(
                    "rq_job_cancel_after_worker_stop_unconfirmed job_id=%s",
                    getattr(job, "id", "unknown"),
                    exc_info=True,
                )
            self._start_locked()
        logger.info(
            "embedded_rq_job_stopped job_id=%s job_record_cancelled=%s",
            getattr(job, "id", "unknown"),
            cancelled,
        )
        return cancelled

    def stop(self) -> None:
        self._shutdown.set()
        with self._lock:
            process = self._process
            self._process = None
            if process is not None:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=8)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=2)
                if process.is_alive():
                    logger.error(
                        "embedded_rq_worker_shutdown_unconfirmed pid=%s",
                        getattr(process, "pid", None),
                    )
                else:
                    close = getattr(process, "close", None)
                    if close:
                        close()
        if self._watcher is not None and self._watcher.is_alive():
            self._watcher.join(timeout=3)
        logger.info("embedded_rq_worker_supervisor_stopped")

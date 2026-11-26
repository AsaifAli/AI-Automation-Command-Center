from types import SimpleNamespace

from app.core.config import Settings
from app.workers import embedded


class FakeProcess:
    def __init__(self, *, target, args, name, daemon):
        self.target = target
        self.args = args
        self.name = name
        self.daemon = daemon
        self.pid = 4242
        self.alive = False
        self.started = False
        self.terminated = False
        self.killed = False
        self.join_timeouts = []

    def start(self):
        self.started = True
        self.alive = True

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.terminated = True
        self.alive = False

    def kill(self):
        self.killed = True
        self.alive = False

    def join(self, timeout=None):
        self.join_timeouts.append(timeout)


class FakeContext:
    def __init__(self):
        self.processes = []

    def Process(self, **kwargs):
        process = FakeProcess(**kwargs)
        self.processes.append(process)
        return process


def test_embedded_worker_starts_child_and_replaces_it_after_stopping_current_job(monkeypatch):
    context = FakeContext()
    monkeypatch.setattr(embedded.multiprocessing, "get_context", lambda method: context)
    worker = embedded.EmbeddedRQWorker(Settings(_env_file=None))

    worker.start()
    first = context.processes[0]
    assert first.started is True
    assert first.daemon is True

    registry_calls = []

    class FakeStartedRegistry:
        def __init__(self, name, *, connection, job_class, serializer):
            registry_calls.append(("init", name, connection, job_class, serializer))

        def remove_executions(self, candidate):
            registry_calls.append(("remove-executions", candidate.id))

    monkeypatch.setattr(embedded, "StartedJobRegistry", FakeStartedRegistry, raising=False)
    job = SimpleNamespace(
        id="run-1",
        origin="aiac-flowpilot",
        worker_name=worker.worker_name,
        connection=object(),
        serializer=None,
        cancelled=False,
        cancel=lambda: (registry_calls.append(("cancel", "run-1")), setattr(job, "cancelled", True)),
    )
    assert worker.stop_current_job(job) is True

    assert registry_calls[0][0:2] == ("init", "aiac-flowpilot")
    assert registry_calls[1] == ("remove-executions", "run-1")
    assert registry_calls[2] == ("cancel", "run-1")
    assert first.terminated is True
    assert first.join_timeouts
    assert job.cancelled is True
    assert len(context.processes) == 2
    assert context.processes[1].started is True

    worker.stop()
    assert context.processes[1].terminated is True


def test_embedded_worker_refuses_to_kill_a_job_owned_by_another_worker(monkeypatch):
    context = FakeContext()
    monkeypatch.setattr(embedded.multiprocessing, "get_context", lambda method: context)
    worker = embedded.EmbeddedRQWorker(Settings(_env_file=None))
    worker.start()

    job = SimpleNamespace(
        id="run-2",
        worker_name="different-worker",
        cancelled=False,
        cancel=lambda: setattr(job, "cancelled", True),
    )
    assert worker.stop_current_job(job) is False
    assert context.processes[0].alive is True
    assert job.cancelled is False

    worker.stop()


def test_embedded_worker_configures_telemetry_inside_child_before_consuming_jobs(monkeypatch):
    calls = []

    class FakeRedis:
        @staticmethod
        def from_url(*_args, **kwargs):
            calls.append("redis")
            calls.append(("redis_options", kwargs))
            return object()

    class FakeSimpleWorker:
        def __init__(self, *_args, **_kwargs):
            self._stop_requested = False
            calls.append("worker-created")

        def work(self, **_kwargs):
            calls.append("work")
            self._stop_requested = True

    monkeypatch.setattr(embedded, "configure_telemetry", lambda _settings: calls.append("telemetry"), raising=False)
    monkeypatch.setattr(embedded, "Redis", FakeRedis)
    monkeypatch.setattr(embedded, "Queue", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(embedded, "SimpleWorker", FakeSimpleWorker)

    settings = Settings(_env_file=None, otel_enabled=False)
    embedded._run_worker(settings, "worker-test")

    assert calls[0] == "telemetry"
    assert calls[1] == "redis"
    redis_options = dict(calls[2][1])
    assert redis_options["socket_timeout"] == settings.workflow_job_timeout_seconds + 120 + 30
    assert calls[3:] == ["worker-created", "work"]


def test_api_forks_embedded_worker_before_initializing_parent_telemetry(monkeypatch):
    import app.api.main as api_module

    calls = []

    class FakeEmbeddedWorker:
        def __init__(self, _settings):
            calls.append("worker-created")

        def start(self):
            calls.append("worker-start")

    monkeypatch.setattr(api_module.settings, "database_url", "")
    monkeypatch.setattr(api_module.settings, "redis_state_enabled", False)
    monkeypatch.setattr(api_module.settings, "embedded_rq_worker_enabled", True)
    monkeypatch.setattr(api_module, "LLMProvider", lambda _settings: calls.append("llm"))
    monkeypatch.setattr(api_module, "EmbeddedRQWorker", FakeEmbeddedWorker)
    monkeypatch.setattr(api_module, "configure_telemetry", lambda _settings: calls.append("telemetry"))
    monkeypatch.setattr(api_module, "embedded_rq_worker", None)

    api_module.startup()

    assert calls == ["llm", "worker-created", "worker-start", "telemetry"]


def test_supervisor_recovers_if_the_worker_process_reference_is_missing(monkeypatch):
    context = FakeContext()
    monkeypatch.setattr(embedded.multiprocessing, "get_context", lambda method: context)
    worker = embedded.EmbeddedRQWorker(Settings(_env_file=None))
    worker.start()
    worker._process = None

    class OneIterationEvent:
        def __init__(self):
            self.calls = 0

        def wait(self, _timeout):
            self.calls += 1
            return self.calls > 1

        def is_set(self):
            return False

    worker._shutdown = OneIterationEvent()
    worker._watch()

    assert len(context.processes) == 2
    assert context.processes[1].started is True

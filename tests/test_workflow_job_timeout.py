import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_workflow_job_timeout_defaults_to_ten_minutes():
    settings = Settings(_env_file=None)

    assert settings.workflow_job_timeout_seconds == 600


@pytest.mark.parametrize("timeout_seconds", [59, 1801])
def test_workflow_job_timeout_rejects_values_outside_safe_bounds(timeout_seconds):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, workflow_job_timeout_seconds=timeout_seconds)

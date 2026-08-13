from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class WorkflowName(str, Enum):
    CONTENT = "content"
    COMPETITOR = "competitor"
    OUTREACH = "outreach"
    KPI = "kpi"


class RunRequest(BaseModel):
    workflow: WorkflowName
    payload: dict[str, Any] = Field(default_factory=dict)


class Evidence(BaseModel):
    source: str
    title: str
    detail: str
    confidence: float = Field(ge=0, le=1)
    url: str | None = None
    observed_at: datetime | None = None


class Usage(BaseModel):
    provider: str = "unknown"
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0


class WorkflowResult(BaseModel):
    run_id: str
    workflow: WorkflowName
    status: str
    output: dict[str, Any]
    evidence: list[Evidence] = Field(default_factory=list)
    duration_ms: int
    created_at: datetime
    usage: Usage | None = None
    warnings: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    demo_mode: bool


class ApprovalRequest(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    reviewer: str = Field(min_length=2, max_length=100)
    note: str = Field(default="", max_length=1000)


class ApprovalResponse(BaseModel):
    approval_id: str
    run_id: str
    decision: str
    reviewer: str
    note: str
    decided_at: datetime


class RunListResponse(BaseModel):
    runs: list[WorkflowResult]
    total: int


class MetricResponse(BaseModel):
    workflow_runs_total: int
    workflow_runs_completed: int
    workflow_runs_failed: int
    workflow_approvals_total: int
    workflow_approvals_approved: int
    workflow_runs_queued: int = 0
    workflow_runs_running: int = 0

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from . import schedule


class JobCreate(BaseModel):
    workspace_id: UUID
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    kind: str = "heartbeat"
    schedule_expr: str | None = None
    tz: str = "UTC"
    expected_runtime_s: int | None = Field(default=None, ge=1)
    grace_s: int = Field(default=300, ge=0)
    environment_id: UUID | None = None
    team_id: UUID | None = None
    tags: list[str] = []
    sla_target: float | None = Field(default=None, ge=0, le=100)

    @field_validator("schedule_expr")
    @classmethod
    def _valid_cron(cls, v):
        if v is not None and not schedule.validate(v):
            raise ValueError("invalid cron expression")
        return v


class JobUpdate(BaseModel):
    description: str | None = None
    schedule_expr: str | None = None
    tz: str | None = None
    expected_runtime_s: int | None = None
    grace_s: int | None = None
    tags: list[str] | None = None
    sla_target: float | None = None
    paused: bool | None = None

    @field_validator("schedule_expr")
    @classmethod
    def _valid_cron(cls, v):
        if v is not None and not schedule.validate(v):
            raise ValueError("invalid cron expression")
        return v


class JobOut(BaseModel):
    id: UUID
    workspace_id: UUID
    name: str
    description: str | None
    kind: str
    schedule_expr: str | None
    schedule_human: str | None
    tz: str
    expected_runtime_s: int | None
    grace_s: int
    tags: list[str]
    status: str
    paused: bool
    last_run_at: datetime | None
    last_status: str | None
    next_expected_at: datetime | None
    reliability_score: int | None
    heartbeat_token: str


class HeartbeatBody(BaseModel):
    status: str = Field(pattern="^(start|success|fail)$")
    execution_id: str | None = None
    sequence: int = 0
    duration_ms: int | None = Field(default=None, ge=0)
    exit_code: int | None = None
    agent_ts: datetime | None = None
    host: str | None = None
    stdout_tail: str | None = None
    stderr_tail: str | None = None
    meta: dict = {}


class ExecutionOut(BaseModel):
    id: str
    job_id: UUID
    status: str
    scheduled_ts: datetime
    agent_ts_start: datetime | None
    agent_ts_end: datetime | None
    duration_ms: int | None
    exit_code: int | None
    host: str | None
    skew_ms: int


class Page(BaseModel):
    items: list
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# R14 — response models for the dashboard read endpoints.
#
# Before R14 only 3 of 77 endpoints declared a response_model, so the OpenAPI spec described almost
# every response as `{"type": "object", "additionalProperties": true}` and the R13 contract test
# had nothing to validate against. These models describe what the routers already return; they are
# documentation and a contract, not a behaviour change — hence extra="allow" nowhere and every
# nullable column typed as optional.
#
# NOTE: adding a model changes error behaviour in one way worth knowing — if a router ever returns
# a row missing a required field, FastAPI now raises a 500 at serialisation instead of passing the
# partial object through. That is the point, but it is a real change.
# ---------------------------------------------------------------------------


class JobPage(BaseModel):
    items: list[JobOut]
    next_cursor: str | None = None


class WorkspaceOut(BaseModel):
    id: UUID
    name: str
    created_at: datetime


class IncidentOut(BaseModel):
    id: UUID
    org_id: UUID
    severity: str
    status: str
    title: str
    correlation_key: str | None = None
    started_at: datetime
    detected_at: datetime | None = None
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    affected_job_ids: list[UUID] = []
    job_names: list[str] | None = None
    root_cause: str | None = None
    resolution: str | None = None
    responder_ids: list[UUID] = []
    rule_id: UUID | None = None
    last_notified_at: datetime | None = None
    notes: list[dict] | None = None
    correlation_signals: list[dict] = []


class ChannelOut(BaseModel):
    id: UUID
    kind: str
    name: str
    rate_per_min: int
    enabled: bool
    created_at: datetime


class AlertRuleOut(BaseModel):
    id: UUID
    org_id: UUID
    name: str
    scope: dict = {}
    condition: str
    params: dict = {}
    severity: str
    channel_ids: list[UUID] = []
    business_hours: dict | None = None
    repeat_interval_s: int | None = None
    enabled: bool
    created_at: datetime


class StatusPageOut(BaseModel):
    id: UUID
    slug: str
    title: str
    visibility: str
    job_ids: list[UUID] = []


class DependencyNode(BaseModel):
    id: UUID
    name: str
    status: str
    last_run_at: datetime | None = None


class DependencyEdge(BaseModel):
    job_id: UUID
    depends_on_job_id: UUID


class DependencyGraph(BaseModel):
    nodes: list[DependencyNode] = []
    edges: list[DependencyEdge] = []


class NamedMetric(BaseModel):
    name: str
    p95_ms: float | None = None
    failures: int | None = None


class OverviewOut(BaseModel):
    total_jobs: int
    # Keyed by job status value — a data-keyed map, not a fixed set of fields.
    by_status: dict[str, int] = {}
    executions_today: int
    success_rate_today: float | None = None
    top_slowest_7d: list[NamedMetric] = []
    top_failing_7d: list[NamedMetric] = []
    mttd_min: float | None = None
    mttr_min: float | None = None

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
    by_status: dict[str, int]          # R32: always set by the handler; a default here told the
                                        # generated client it could be missing
    executions_today: int
    success_rate_today: float | None = None
    top_slowest_7d: list[NamedMetric]
    top_failing_7d: list[NamedMetric]
    mttd_min: float | None = None
    mttr_min: float | None = None


# ---------------------------------------------------------------------------
# R15 — models for the reads R14 left as bare dicts. Same rule as R14: these describe what the
# routers already return, verified by the suite passing unchanged.
# ---------------------------------------------------------------------------


class AgentOut(BaseModel):
    id: UUID
    kind: str
    name: str | None = None
    host_id: str | None = None
    version: str | None = None
    status: str | None = None
    last_seen_at: datetime | None = None
    skew_ms: int | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    jobs: int = 0


class ClusterOut(BaseModel):
    id: UUID
    name: str
    scope: str | None = None
    namespaces: list[str] | None = None
    last_seen_at: datetime | None = None
    version: str | None = None
    cronjobs: int = 0
    failing: int = 0


class TopologyJob(BaseModel):
    id: UUID
    name: str
    status: str


class TopologyUser(BaseModel):
    name: str
    status: str
    jobs: list[TopologyJob] = []


class TopologyServer(BaseModel):
    id: UUID
    name: str | None = None
    status: str
    users: list[TopologyUser] = []


class TopologyNamespace(BaseModel):
    name: str
    status: str
    jobs: list[TopologyJob] = []


class TopologyCluster(BaseModel):
    id: UUID
    name: str
    status: str
    namespaces: list[TopologyNamespace] = []


class TopologyOut(BaseModel):
    servers: list[TopologyServer] = []
    clusters: list[TopologyCluster] = []
    # Jobs attached to neither a server nor a cluster (pure heartbeat jobs).
    heartbeat_only: list[TopologyJob] = []
    status: str


class LogHit(BaseModel):
    """Loose by design: a log row's columns depend on the backing store."""

    model_config = {"extra": "allow"}


class LogSearchOut(BaseModel):
    items: list[LogHit] = []
    hosts: list[str] = []


class SeriesPoint(BaseModel):
    t: datetime
    executions: int
    ok: int
    failed: int
    missed: int
    p50_ms: float | None = None
    p95_ms: float | None = None


class SeriesIncident(BaseModel):
    model_config = {"extra": "allow"}


class SeriesOut(BaseModel):
    bucket: str
    points: list[SeriesPoint] = []
    incidents: list[SeriesIncident] = []


class JobAnalyticsOut(BaseModel):
    id: UUID
    name: str
    status: str
    reliability_score: int | None = None
    sla_target: float | None = None
    tags: list[str] = []
    runs: int
    ok: int
    failures: int
    missed: int
    success_rate: float | None = None
    p50_ms: float | None = None
    p95_ms: float | None = None
    max_ms: int | None = None
    drift_pct: float | None = None
    sla_met: bool | None = None
    est_cost_usd: float | None = None


# ---------------------------------------------------------------------------
# R16 — R4's integrations reads. These were missed by R14/R15 because the UNMODELLED guard only
# checked a hand-listed set of paths, not every GET; the guard now scans the route table.
# ---------------------------------------------------------------------------


class SignalDestinationOut(BaseModel):
    id: UUID
    name: str
    kind: str
    event_types: list[str] = []
    workspace_ids: list[UUID] | None = None
    enabled: bool
    consecutive_failures: int = 0
    disabled_reason: str | None = None
    created_at: datetime
    sent_24h: int = 0
    failed_24h: int = 0
    url: str | None = None
    has_secret: bool = False
    subject_prefix: str | None = None   # nats destinations only
    has_token: bool = False             # nats destinations only
    # Never the secret itself — only whether one is configured.


class SignalDeliveryOut(BaseModel):
    id: UUID
    destination_id: UUID
    destination: str
    signal_id: str
    event_type: str
    status: str
    attempts: int
    last_error: str | None = None
    response_code: int | None = None
    created_at: datetime
    sent_at: datetime | None = None


class SignalSchemaOut(BaseModel):
    schema_: str = Field(alias="schema")
    version: int | str
    event_types: list[str]
    headers: list[str]
    signature: str
    idempotency: str
    example: dict

    model_config = {"populate_by_name": True}


class MonitoringGapOut(BaseModel):
    """R25: a period when the platform itself was not watching. Slots whose deadline fell inside it
    are `unobserved`, never `missed`, and never alert."""
    id: int
    service: str
    started_at: datetime
    ended_at: datetime
    slots_unobserved: int


class MonitoringGapsOut(BaseModel):
    gaps: list[MonitoringGapOut]
    unobserved_slots_30d: int   # for this organisation

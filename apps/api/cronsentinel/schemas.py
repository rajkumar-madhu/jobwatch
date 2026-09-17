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

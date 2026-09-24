package agent

import "time"

// Event mirrors apps/api processor.process() input. Values are never included for env vars.
type Event struct {
	Kind        string            `json:"kind"`                  // start|success|fail|progress
	JobToken    string            `json:"job_token,omitempty"`   // resolved via discovery map
	Fingerprint string            `json:"fingerprint,omitempty"` // fallback when token unknown
	ExecutionID string            `json:"execution_id"`
	Sequence    int               `json:"sequence"`
	AgentTS     time.Time         `json:"agent_ts"`
	MonotonicNS int64             `json:"monotonic_ns"`
	DurationMS  *int64            `json:"duration_ms,omitempty"`
	ExitCode    *int              `json:"exit_code,omitempty"`
	Host        string            `json:"host"`
	StdoutTail  string            `json:"stdout_tail,omitempty"`
	StderrTail  string            `json:"stderr_tail,omitempty"`
	EnvVarNames []string          `json:"env_var_names,omitempty"`
	Meta        map[string]string `json:"meta,omitempty"`
}

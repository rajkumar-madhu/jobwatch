package agent

import (
	"encoding/json"
	"errors"
	"os"
	"time"
)

// Config is loaded from /etc/cronsentinel/agent.json (written by `enroll`).
type Config struct {
	ServerURL        string        `json:"server_url"`          // https://ingest.example.com
	AgentID          string        `json:"agent_id"`
	AgentKey         string        `json:"agent_key"`           // long-lived, hashed server-side
	HostID           string        `json:"host_id"`
	Name             string        `json:"name"`
	DiscoveryEvery   time.Duration `json:"discovery_every"`     // default 5m
	MetricsEvery     time.Duration `json:"metrics_every"`       // default 30s
	FlushEvery       time.Duration `json:"flush_every"`         // default 5s
	BatchSize        int           `json:"batch_size"`          // default 100
	BufferPath       string        `json:"buffer_path"`         // default /var/lib/cronsentinel/buffer.jsonl
	PassiveJournal   bool          `json:"passive_journal"`     // watch cron journal for non-wrapped jobs
	LogTailBytes     int           `json:"log_tail_bytes"`      // default 32768
	EnvNameAllowlist []string      `json:"env_name_allowlist"`  // env var NAMES only, never values
	InsecureSkipTLS  bool          `json:"insecure_skip_tls"`   // dev only
}

func Defaults() Config {
	return Config{
		DiscoveryEvery: 5 * time.Minute, MetricsEvery: 30 * time.Second, FlushEvery: 5 * time.Second,
		BatchSize: 100, BufferPath: "/var/lib/cronsentinel/buffer.jsonl", PassiveJournal: true, LogTailBytes: 32 * 1024,
		EnvNameAllowlist: []string{"PATH", "HOME", "SHELL", "USER", "LANG", "TZ"},
	}
}

func Load(path string) (Config, error) {
	c := Defaults()
	b, err := os.ReadFile(path)
	if err != nil {
		return c, err
	}
	if err := json.Unmarshal(b, &c); err != nil {
		return c, err
	}
	if c.ServerURL == "" || c.AgentKey == "" {
		return c, errors.New("config missing server_url/agent_key — run `cronsentinel-agent enroll`")
	}
	return c, nil
}

func (c Config) Save(path string) error {
	b, _ := json.MarshalIndent(c, "", "  ")
	return os.WriteFile(path, b, 0o600)
}

package agent

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"time"
)

type Client struct {
	cfg  Config
	http *http.Client
}

func NewClient(cfg Config) *Client {
	tr := &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12, InsecureSkipVerify: cfg.InsecureSkipTLS}}
	return &Client{cfg: cfg, http: &http.Client{Transport: tr, Timeout: 20 * time.Second}}
}

func (c *Client) post(ctx context.Context, path string, body any, out any) error {
	b, _ := json.Marshal(body)
	req, err := http.NewRequestWithContext(ctx, "POST", c.cfg.ServerURL+path, bytes.NewReader(b))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Agent-Key", c.cfg.AgentKey)
	req.Header.Set("X-Agent-Id", c.cfg.AgentID)
	req.Header.Set("User-Agent", "cronsentinel-agent/"+Version)
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	rb, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode == 401 || resp.StatusCode == 403 {
		return &FatalError{fmt.Sprintf("auth rejected (%d): %s", resp.StatusCode, rb)}
	}
	if resp.StatusCode >= 300 {
		return fmt.Errorf("server %d: %s", resp.StatusCode, rb)
	}
	if out != nil && len(rb) > 0 {
		return json.Unmarshal(rb, out)
	}
	return nil
}

type FatalError struct{ Msg string }

func (e *FatalError) Error() string { return e.Msg }

func IsFatal(err error) bool { var f *FatalError; return errors.As(err, &f) }

// Enroll exchanges a one-time bootstrap token for a long-lived agent key (D8).
func Enroll(ctx context.Context, serverURL, bootstrapToken, name, hostID string, insecure bool) (Config, error) {
	cfg := Defaults()
	cfg.ServerURL, cfg.Name, cfg.HostID, cfg.InsecureSkipTLS = serverURL, name, hostID, insecure
	c := NewClient(cfg)
	var out struct {
		AgentID string `json:"agent_id"`
		Key     string `json:"agent_key"`
	}
	body := map[string]string{"bootstrap_token": bootstrapToken, "name": name, "host_id": hostID, "kind": "linux", "version": Version}
	if err := c.post(ctx, "/agent/v1/enroll", body, &out); err != nil {
		return cfg, err
	}
	cfg.AgentID, cfg.AgentKey = out.AgentID, out.Key
	return cfg, nil
}

func (c *Client) SendEvents(ctx context.Context, evs []json.RawMessage) error {
	return c.post(ctx, "/agent/v1/events", map[string]any{"events": evs}, nil)
}

func (c *Client) SendDiscovery(ctx context.Context, jobs []DiscoveredJob) (map[string]string, error) {
	var out struct {
		Tokens map[string]string `json:"tokens"` // fingerprint -> heartbeat token
	}
	err := c.post(ctx, "/agent/v1/discovery", map[string]any{"jobs": jobs}, &out)
	return out.Tokens, err
}

func (c *Client) SendMetrics(ctx context.Context, m map[string]any) error {
	return c.post(ctx, "/agent/v1/metrics", m, nil)
}

// Heartbeat reports liveness plus the interval the server should expect it at, so the server
// can declare this agent offline relative to *its* cadence rather than a global constant.
func (c *Client) Heartbeat(ctx context.Context, every time.Duration) error {
	return c.post(ctx, "/agent/v1/heartbeat", map[string]any{
		"version": Version, "agent_ts": time.Now().UTC(), "heartbeat_interval_s": int(every.Seconds()),
	}, nil)
}

const Version = "0.1.0"

type DiscoveredJob struct {
	Fingerprint string `json:"fingerprint"`
	Kind        string `json:"kind"` // cron|systemd
	Name        string `json:"name"`
	Schedule    string `json:"schedule"`
	Command     string `json:"command"`
	User        string `json:"user"`
	Source      string `json:"source"` // file path or unit name
	WorkingDir  string `json:"working_dir,omitempty"`
}

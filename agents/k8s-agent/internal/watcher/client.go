package watcher

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"time"
)

const Version = "0.1.0"

// Client posts to the ingest tier with the agent key obtained at enrollment.
type Client struct {
	server, agentID, agentKey string
	http                      *http.Client
}

func NewClient(server, agentID, agentKey string, insecure bool) *Client {
	return &Client{server: server, agentID: agentID, agentKey: agentKey,
		http: &http.Client{Timeout: 20 * time.Second, Transport: &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12, InsecureSkipVerify: insecure}}}}
}

func (c *Client) Post(ctx context.Context, path string, body any, out any) error {
	b, _ := json.Marshal(body)
	req, _ := http.NewRequestWithContext(ctx, "POST", c.server+path, bytes.NewReader(b))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Agent-Key", c.agentKey)
	req.Header.Set("X-Agent-Id", c.agentID)
	req.Header.Set("User-Agent", "cronsentinel-k8s/"+Version)
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	rb, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode >= 300 {
		return fmt.Errorf("server %d: %s", resp.StatusCode, rb)
	}
	if out != nil && len(rb) > 0 {
		return json.Unmarshal(rb, out)
	}
	return nil
}

// Enroll exchanges a bootstrap token for an agent key; result cached in a mounted emptyDir/Secret path.
func Enroll(ctx context.Context, server, bootstrap, clusterName, insecure string) (id, key string, err error) {
	c := &Client{server: server, http: &http.Client{Timeout: 20 * time.Second, Transport: &http.Transport{TLSClientConfig: &tls.Config{InsecureSkipVerify: insecure == "true"}}}}
	var out struct {
		AgentID string `json:"agent_id"`
		Key     string `json:"agent_key"`
	}
	host, _ := os.Hostname()
	err = c.Post(ctx, "/agent/v1/enroll", map[string]string{"bootstrap_token": bootstrap, "name": clusterName, "host_id": "k8s:" + clusterName, "kind": "k8s", "version": Version}, &out)
	return out.AgentID, out.Key, err
}

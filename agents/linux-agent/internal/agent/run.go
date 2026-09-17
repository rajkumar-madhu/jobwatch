package agent

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"syscall"
	"time"
)

// tailWriter keeps only the last n bytes written.
type tailWriter struct {
	buf []byte
	n   int
}

func (t *tailWriter) Write(p []byte) (int, error) {
	t.buf = append(t.buf, p...)
	if len(t.buf) > t.n {
		t.buf = t.buf[len(t.buf)-t.n:]
	}
	return len(p), nil
}

func newExecID() string {
	b := make([]byte, 12)
	rand.Read(b)
	return fmt.Sprintf("%d-%s", time.Now().UnixMilli(), hex.EncodeToString(b))
}

func envNames(allow []string) []string {
	set := map[string]bool{}
	for _, a := range allow {
		set[a] = true
	}
	var out []string
	for _, kv := range os.Environ() {
		k := strings.SplitN(kv, "=", 2)[0]
		if set[k] {
			out = append(out, k) // names only — values are never captured
		}
	}
	return out
}

// Run is the `cs-run` wrapper: emits start → (progress every 30s) → success|fail with exit code and log tails.
// Exit code of the wrapper == exit code of the child, so cron mail/behaviour is preserved.
func Run(ctx context.Context, cfg Config, push func(Event) error, jobToken, fingerprint string, argv []string) int {
	host, _ := os.Hostname()
	execID := newExecID()
	seq := 0
	emit := func(kind string, mutate func(*Event)) {
		ev := Event{Kind: kind, JobToken: jobToken, Fingerprint: fingerprint, ExecutionID: execID, Sequence: seq,
			AgentTS: time.Now().UTC(), MonotonicNS: int64(time.Since(startMono)), Host: host, Meta: map[string]string{"wrapper": "cs-run", "agent_id": cfg.AgentID}}
		if mutate != nil {
			mutate(&ev)
		}
		_ = push(ev)
		seq++
	}
	emit("start", func(e *Event) { e.EnvVarNames = envNames(cfg.EnvNameAllowlist); e.Meta["command"] = strings.Join(argv, " ") })

	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	so, se := &tailWriter{n: cfg.LogTailBytes}, &tailWriter{n: cfg.LogTailBytes}
	cmd.Stdout = io.MultiWriter(os.Stdout, so)
	cmd.Stderr = io.MultiWriter(os.Stderr, se)
	cmd.Stdin = os.Stdin
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	t0 := time.Now()
	err := cmd.Start()
	if err != nil {
		code := 127
		emit("fail", func(e *Event) { e.ExitCode = &code; e.StderrTail = err.Error() })
		return code
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ticker.C:
			emit("progress", nil)
		case werr := <-done:
			dur := time.Since(t0).Milliseconds()
			code := 0
			if werr != nil {
				if ee, ok := werr.(*exec.ExitError); ok {
					code = ee.ExitCode()
				} else {
					code = 1
				}
			}
			kind := "success"
			if code != 0 {
				kind = "fail"
			}
			emit(kind, func(e *Event) {
				e.ExitCode = &code
				e.DurationMS = &dur
				e.StdoutTail = string(bytes.ToValidUTF8(so.buf, []byte("?")))
				e.StderrTail = string(bytes.ToValidUTF8(se.buf, []byte("?")))
			})
			return code
		}
	}
}

var startMono = time.Now()

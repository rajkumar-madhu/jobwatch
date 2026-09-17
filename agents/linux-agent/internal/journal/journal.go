// Package journal watches cron's journal for "CMD (...)" lines to emit passive start events for
// jobs not wrapped by cs-run. End-of-run is approximated by polling the child pid.
// STUB: exit code unknown in passive mode (reported as meta.passive=true, exit_code omitted).
package journal

import (
	"bufio"
	"context"
	"os"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"time"
)

var cmdRe = regexp.MustCompile(`CRON\[(\d+)\]: \((\S+)\) CMD \((.*)\)$`)

type Start struct {
	PID  int
	User string
	Cmd  string
	TS   time.Time
}

// Watch streams cron CMD lines. onStart is called for each; onEnd fires when the pid disappears.
func Watch(ctx context.Context, onStart func(Start), onEnd func(Start, time.Duration)) error {
	c := exec.CommandContext(ctx, "journalctl", "-f", "-n", "0", "-o", "short-iso", "_COMM=cron", "_COMM=crond")
	out, err := c.StdoutPipe()
	if err != nil {
		return err
	}
	if err := c.Start(); err != nil {
		return err
	}
	sc := bufio.NewScanner(out)
	for sc.Scan() {
		m := cmdRe.FindStringSubmatch(sc.Text())
		if m == nil {
			continue
		}
		pid, _ := strconv.Atoi(m[1])
		st := Start{PID: pid, User: m[2], Cmd: strings.TrimSpace(m[3]), TS: time.Now().UTC()}
		onStart(st)
		go func(st Start) {
			t0 := time.Now()
			for {
				if _, err := os.Stat("/proc/" + strconv.Itoa(st.PID)); err != nil {
					onEnd(st, time.Since(t0))
					return
				}
				select {
				case <-ctx.Done():
					return
				case <-time.After(2 * time.Second):
				}
			}
		}(st)
	}
	return c.Wait()
}

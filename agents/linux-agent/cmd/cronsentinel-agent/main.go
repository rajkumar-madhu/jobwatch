// cronsentinel-agent: discover + monitor scheduled jobs on a Linux host.
//
//	cronsentinel-agent enroll --server https://ingest.example.com --token <bootstrap> [--name host]
//	cronsentinel-agent run                       # daemon (systemd)
//	cronsentinel-agent exec [--job <token>] -- <cmd> [args...]   # cs-run wrapper
//	cronsentinel-agent discover                  # print discovered jobs as JSON
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/cronsentinel/linux-agent/internal/agent"
	"github.com/cronsentinel/linux-agent/internal/buffer"
	"github.com/cronsentinel/linux-agent/internal/discovery"
	"github.com/cronsentinel/linux-agent/internal/hostmetrics"
	"github.com/cronsentinel/linux-agent/internal/journal"
)

const defaultConfigPath = "/etc/cronsentinel/agent.json"

// configPath is /etc/cronsentinel/agent.json unless CRONSENTINEL_CONFIG overrides it — for running
// the agent unprivileged (tests, a per-user install). The buffer path is inside the config itself.
var configPath = func() string {
	if p := os.Getenv("CRONSENTINEL_CONFIG"); p != "" {
		return p
	}
	return defaultConfigPath
}()

func hostID() string {
	if b, err := os.ReadFile("/etc/machine-id"); err == nil && len(b) > 8 {
		return strings.TrimSpace(string(b))
	}
	h, _ := os.Hostname()
	return h
}

func main() {
	if strings.HasSuffix(os.Args[0], "cs-run") { // symlink alias: cs-run [--job TOKEN] -- cmd
		os.Exit(cmdExec(os.Args[1:]))
	}
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: cronsentinel-agent enroll|run|exec|discover")
		os.Exit(2)
	}
	switch os.Args[1] {
	case "enroll":
		cmdEnroll(os.Args[2:])
	case "run":
		cmdRun()
	case "exec":
		os.Exit(cmdExec(os.Args[2:]))
	case "discover":
		h := hostID()
		jobs := append(discovery.Crontabs(h), discovery.SystemdTimers(h)...)
		json.NewEncoder(os.Stdout).Encode(jobs)
	default:
		fmt.Fprintln(os.Stderr, "unknown command")
		os.Exit(2)
	}
}

func cmdEnroll(args []string) {
	fs := flag.NewFlagSet("enroll", flag.ExitOnError)
	server := fs.String("server", "", "ingest server URL")
	token := fs.String("token", "", "one-time bootstrap token")
	name := fs.String("name", "", "agent display name (default hostname)")
	insecure := fs.Bool("insecure", false, "skip TLS verification (dev only)")
	fs.Parse(args)
	if *server == "" || *token == "" {
		log.Fatal("--server and --token required")
	}
	if *name == "" {
		*name, _ = os.Hostname()
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	cfg, err := agent.Enroll(ctx, *server, *token, *name, hostID(), *insecure)
	if err != nil {
		log.Fatalf("enroll failed: %v", err)
	}
	os.MkdirAll(filepath.Dir(configPath), 0o750)
	if err := cfg.Save(configPath); err != nil {
		log.Fatalf("save config: %v", err)
	}
	fmt.Println("enrolled: agent_id =", cfg.AgentID, "→ config written to", configPath)
}

// exec: wrapper mode. Reads config for server (buffer only; the daemon flushes).
func cmdExec(args []string) int {
	fs := flag.NewFlagSet("exec", flag.ContinueOnError)
	jobToken := fs.String("job", "", "heartbeat token (optional; fingerprint used otherwise)")
	fs.Parse(args)
	argv := fs.Args()
	if len(argv) > 0 && argv[0] == "--" {
		argv = argv[1:]
	}
	if len(argv) == 0 {
		fmt.Fprintln(os.Stderr, "usage: cronsentinel-agent exec [--job TOKEN] -- cmd args...")
		return 2
	}
	cfg, err := agent.Load(configPath)
	if err != nil {
		log.Printf("warning: %v — running command unmonitored", err)
		cfg = agent.Defaults()
	}
	buf, err := buffer.Open(cfg.BufferPath)
	if err != nil {
		log.Printf("warning: buffer unavailable: %v", err)
	}
	push := func(ev agent.Event) error {
		if buf == nil {
			return nil
		}
		return buf.Push(ev)
	}
	user := os.Getenv("USER")
	fp := discovery.Fingerprint(cfg.HostID, user, strings.Join(argv, " "), os.Getenv("CS_SCHEDULE"))
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer cancel()
	code := agent.Run(ctx, cfg, push, *jobToken, fp, argv)
	if buf != nil {
		buf.Close()
	}
	return code
}

func cmdRun() {
	cfg, err := agent.Load(configPath)
	if err != nil {
		log.Fatal(err)
	}
	buf, err := buffer.Open(cfg.BufferPath)
	if err != nil {
		log.Fatal(err)
	}
	defer buf.Close()
	client := agent.NewClient(cfg)
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer cancel()
	var wg sync.WaitGroup
	var tokMu sync.RWMutex
	tokens := map[string]string{}

	// 1. flusher: buffer → server, exponential backoff, resumes from committed offset
	wg.Add(1)
	go func() {
		defer wg.Done()
		backoff := cfg.FlushEvery
		for {
			select {
			case <-ctx.Done():
				return
			case <-time.After(backoff):
			}
			lines, next, err := buf.Peek(cfg.BatchSize)
			if err != nil || len(lines) == 0 {
				backoff = cfg.FlushEvery
				continue
			}
			if err := client.SendEvents(ctx, lines); err != nil {
				if agent.IsFatal(err) {
					log.Fatalf("fatal: %v", err)
				}
				log.Printf("send failed (%d queued): %v", len(lines), err)
				if backoff < 5*time.Minute {
					backoff *= 2
				}
				continue
			}
			buf.Ack(next)
			backoff = cfg.FlushEvery
		}
	}()

	// 2. discovery
	wg.Add(1)
	go func() {
		defer wg.Done()
		for {
			jobs := append(discovery.Crontabs(cfg.HostID), discovery.SystemdTimers(cfg.HostID)...)
			if t, err := client.SendDiscovery(ctx, jobs); err != nil {
				log.Printf("discovery failed: %v", err)
			} else {
				tokMu.Lock()
				tokens = t
				tokMu.Unlock()
				log.Printf("discovery: %d jobs", len(jobs))
			}
			select {
			case <-ctx.Done():
				return
			case <-time.After(cfg.DiscoveryEvery):
			}
		}
	}()

	// 3. host metrics + liveness heartbeat
	wg.Add(1)
	go func() {
		defer wg.Done()
		hostmetrics.Collect("/") // prime cpu counters
		for {
			select {
			case <-ctx.Done():
				return
			case <-time.After(cfg.MetricsEvery):
			}
			m := hostmetrics.Collect("/")
			b, _ := json.Marshal(m)
			var mm map[string]any
			json.Unmarshal(b, &mm)
			if err := client.SendMetrics(ctx, mm); err != nil {
				log.Printf("metrics failed: %v", err)
			}
			_ = client.Heartbeat(ctx, cfg.MetricsEvery)
		}
	}()

	// 4. passive journal watcher for non-wrapped cron jobs
	if cfg.PassiveJournal {
		wg.Add(1)
		go func() {
			defer wg.Done()
			host, _ := os.Hostname()
			execIDs := map[int]string{}
			var mu sync.Mutex
			for ctx.Err() == nil {
				err := journal.Watch(ctx,
					func(st journal.Start) {
						if strings.Contains(st.Cmd, "cronsentinel-agent exec") || strings.Contains(st.Cmd, "cs-run") {
							return // wrapped jobs report themselves
						}
						fp := discovery.Fingerprint(cfg.HostID, st.User, st.Cmd, "")
						tokMu.RLock()
						tok := tokens[fp]
						tokMu.RUnlock()
						id := fmt.Sprintf("passive-%d-%d", st.PID, st.TS.UnixMilli())
						mu.Lock()
						execIDs[st.PID] = id
						mu.Unlock()
						buf.Push(agent.Event{Kind: "start", JobToken: tok, Fingerprint: fp, ExecutionID: id, Sequence: 0, AgentTS: st.TS, Host: host,
							Meta: map[string]string{"passive": "true", "command": st.Cmd, "agent_id": cfg.AgentID}})
					},
					func(st journal.Start, d time.Duration) {
						mu.Lock()
						id := execIDs[st.PID]
						delete(execIDs, st.PID)
						mu.Unlock()
						fp := discovery.Fingerprint(cfg.HostID, st.User, st.Cmd, "")
						tokMu.RLock()
						tok := tokens[fp]
						tokMu.RUnlock()
						ms := d.Milliseconds()
						// STUB: exit code unknowable in passive mode → reported as success with passive flag
						buf.Push(agent.Event{Kind: "success", JobToken: tok, Fingerprint: fp, ExecutionID: id, Sequence: 1, AgentTS: time.Now().UTC(), Host: host,
							DurationMS: &ms, Meta: map[string]string{"passive": "true", "exit_code_unknown": "true", "agent_id": cfg.AgentID}})
					})
				if err != nil && ctx.Err() == nil {
					log.Printf("journal watcher exited: %v (retrying in 30s)", err)
					time.Sleep(30 * time.Second)
				}
			}
		}()
	}

	log.Printf("cronsentinel-agent %s running (agent_id=%s)", agent.Version, cfg.AgentID)
	<-ctx.Done()
	wg.Wait()
}

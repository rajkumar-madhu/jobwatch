# Linux Agent

Go 1.23, stdlib only, CGO_ENABLED=0 static binary (~6 MB). Runs as `cronsentinel-agent run` under systemd.

## Modes
| Mode | How | Data captured |
|---|---|---|
| Wrapper (`cs-run -- cmd`) | Replace command in crontab with `cs-run -- <cmd>` | start, progress/30s, exit code, duration, stdout/stderr tail (32KB), env var **names** (allow-listed) |
| Passive (journal) | Watches `journalctl _COMM=cron` for `CMD (...)` lines | start + duration by pid polling. **Exit code unknown** (STUB) |
| Discovery | `/etc/crontab`, `/etc/cron.d/*`, user spools, `systemctl list-timers` every 5m | schedule, command, user, source → jobs auto-created with `discovered` tag |

## Enrollment (D8)
1. Admin: `POST /api/v1/agents/bootstrap-token` → `csb_…` (24h, one-time)
2. Host: `cronsentinel-agent enroll --server https://ingest --token csb_…` → `/etc/cronsentinel/agent.json` with `csa_…` key
3. Key hashed (argon2) server-side; `POST /api/v1/agents/{id}/rotate|revoke`

## Buffering
Append-only JSONL at `/var/lib/cronsentinel/buffer.jsonl` + committed `.offset`. Flush every 5s in batches of 100, exponential backoff to 5m, compacts when fully acked. Survives agent restart and network outage; server dedups on `(execution_id, sequence)`.

## Security
- TLS 1.2+ enforced; `--insecure` dev only
- Never sends env var values, only allow-listed names
- systemd unit: `ProtectSystem=strict`, `NoNewPrivileges`, `MemoryMax=128M`, `CPUQuota=20%`
- Discovery-only mode never modifies crontabs

## Build
```bash
cd agents/linux-agent && make build   # dist/cronsentinel-agent-linux-{amd64,arm64}
go test ./...
```

## Not verified in this delivery
Go toolchain unavailable in the build sandbox — `go build` / `go vet` / `go test` not run. Expect minor compile fixes (imports, unused vars). Logic paths reviewed by hand.

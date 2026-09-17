// Package discovery finds scheduled jobs on the host: system/user crontabs and systemd timers.
package discovery

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/cronsentinel/linux-agent/internal/agent"
)

var wsRe = regexp.MustCompile(`\s+`)

// Fingerprint (D7): sha256(host_id|user|normalized_command|schedule)
func Fingerprint(hostID, user, cmd, schedule string) string {
	norm := wsRe.ReplaceAllString(strings.TrimSpace(cmd), " ")
	h := sha256.Sum256([]byte(hostID + "|" + user + "|" + norm + "|" + strings.TrimSpace(schedule)))
	return hex.EncodeToString(h[:])[:32]
}

func nameFromCommand(cmd string) string {
	f := strings.Fields(cmd)
	for _, tok := range f {
		if strings.HasPrefix(tok, "-") || strings.Contains(tok, "=") {
			continue
		}
		if b := filepath.Base(tok); b != "sh" && b != "bash" && b != "python" && b != "python3" && b != "cs-run" && b != "cronsentinel-agent" {
			return b
		}
	}
	if len(f) > 0 {
		return filepath.Base(f[len(f)-1])
	}
	return "job"
}

// parseCrontab parses crontab lines. systemWide=true expects a user column (/etc/crontab, /etc/cron.d).
func parseCrontab(r *bufio.Scanner, hostID, source, defaultUser string, systemWide bool) []agent.DiscoveredJob {
	var out []agent.DiscoveredJob
	for r.Scan() {
		line := strings.TrimSpace(r.Text())
		if line == "" || strings.HasPrefix(line, "#") || (strings.Contains(line, "=") && !strings.HasPrefix(line, "@") && !strings.ContainsAny(line[:1], "0123456789*")) {
			continue // env assignment like PATH=...
		}
		var schedule, user, cmd string
		if strings.HasPrefix(line, "@") {
			parts := strings.Fields(line)
			if len(parts) < 2 {
				continue
			}
			schedule = parts[0]
			rest := parts[1:]
			if systemWide && len(rest) >= 2 {
				user, cmd = rest[0], strings.Join(rest[1:], " ")
			} else {
				user, cmd = defaultUser, strings.Join(rest, " ")
			}
		} else {
			parts := strings.Fields(line)
			need := 6
			if systemWide {
				need = 7
			}
			if len(parts) < need {
				continue
			}
			schedule = strings.Join(parts[:5], " ")
			if systemWide {
				user, cmd = parts[5], strings.Join(parts[6:], " ")
			} else {
				user, cmd = defaultUser, strings.Join(parts[5:], " ")
			}
		}
		out = append(out, agent.DiscoveredJob{
			Fingerprint: Fingerprint(hostID, user, cmd, schedule), Kind: "cron", Name: nameFromCommand(cmd),
			Schedule: normalizeSpecial(schedule), Command: cmd, User: user, Source: source,
		})
	}
	return out
}

func normalizeSpecial(s string) string {
	switch s {
	case "@hourly":
		return "0 * * * *"
	case "@daily", "@midnight":
		return "0 0 * * *"
	case "@weekly":
		return "0 0 * * 0"
	case "@monthly":
		return "0 0 1 * *"
	case "@yearly", "@annually":
		return "0 0 1 1 *"
	case "@reboot":
		return "" // no schedule; still tracked as a job
	}
	return s
}

func scanFile(path, hostID, defaultUser string, systemWide bool) []agent.DiscoveredJob {
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	return parseCrontab(bufio.NewScanner(f), hostID, path, defaultUser, systemWide)
}

// Crontabs discovers /etc/crontab, /etc/cron.d/*, /var/spool/cron/crontabs/* and /var/spool/cron/*.
func Crontabs(hostID string) []agent.DiscoveredJob {
	var out []agent.DiscoveredJob
	out = append(out, scanFile("/etc/crontab", hostID, "root", true)...)
	if entries, err := os.ReadDir("/etc/cron.d"); err == nil {
		for _, e := range entries {
			if !e.IsDir() && !strings.HasPrefix(e.Name(), ".") {
				out = append(out, scanFile(filepath.Join("/etc/cron.d", e.Name()), hostID, "root", true)...)
			}
		}
	}
	for _, spool := range []string{"/var/spool/cron/crontabs", "/var/spool/cron"} {
		if entries, err := os.ReadDir(spool); err == nil {
			for _, e := range entries {
				if !e.IsDir() {
					out = append(out, scanFile(filepath.Join(spool, e.Name()), hostID, e.Name(), false)...)
				}
			}
		}
	}
	return out
}

// SystemdTimers lists active timers via systemctl (no D-Bus dependency).
func SystemdTimers(hostID string) []agent.DiscoveredJob {
	cmd := exec.Command("systemctl", "list-timers", "--all", "--no-legend", "--no-pager", "--output=json")
	raw, err := cmd.Output()
	if err != nil {
		return systemdTimersLegacy(hostID)
	}
	// Output: [{"next":..,"unit":"x.timer","activates":"x.service",...}]
	type row struct {
		Unit      string `json:"unit"`
		Activates string `json:"activates"`
	}
	var rows []row
	if err := jsonUnmarshal(raw, &rows); err != nil {
		return systemdTimersLegacy(hostID)
	}
	var out []agent.DiscoveredJob
	for _, r := range rows {
		sched := unitProp(r.Unit, "TimersCalendar")
		if sched == "" {
			sched = unitProp(r.Unit, "TimersMonotonic")
		}
		execStart := unitProp(r.Activates, "ExecStart")
		if i := strings.Index(execStart, "argv[]="); i >= 0 {
			execStart = strings.TrimSpace(strings.SplitN(execStart[i+7:], ";", 2)[0])
		}
		out = append(out, agent.DiscoveredJob{
			Fingerprint: Fingerprint(hostID, "systemd", r.Activates, sched), Kind: "systemd", Name: strings.TrimSuffix(r.Unit, ".timer"),
			Schedule: sched, Command: execStart, User: unitProp(r.Activates, "User"), Source: r.Unit,
		})
	}
	return out
}

func systemdTimersLegacy(hostID string) []agent.DiscoveredJob {
	raw, err := exec.Command("systemctl", "list-timers", "--all", "--no-legend", "--no-pager").Output()
	if err != nil {
		return nil
	}
	var out []agent.DiscoveredJob
	for _, line := range strings.Split(string(raw), "\n") {
		f := strings.Fields(line)
		for _, tok := range f {
			if strings.HasSuffix(tok, ".timer") {
				svc := strings.TrimSuffix(tok, ".timer") + ".service"
				sched := unitProp(tok, "TimersCalendar")
				out = append(out, agent.DiscoveredJob{Fingerprint: Fingerprint(hostID, "systemd", svc, sched), Kind: "systemd",
					Name: strings.TrimSuffix(tok, ".timer"), Schedule: sched, Command: unitProp(svc, "ExecStart"), User: "systemd", Source: tok})
				break
			}
		}
	}
	return out
}

func unitProp(unit, prop string) string {
	raw, err := exec.Command("systemctl", "show", unit, "-p", prop, "--value").Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(raw))
}

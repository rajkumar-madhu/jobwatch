package discovery

import (
	"bufio"
	"strings"
	"testing"
)

func TestParseSystemCrontab(t *testing.T) {
	src := `SHELL=/bin/sh
PATH=/usr/bin
# comment
0 2 * * * root /usr/local/bin/backup.sh --full
@daily backup /opt/scripts/rotate.sh
*/5 * * * * www-data php /var/www/artisan schedule:run`
	jobs := parseCrontab(bufio.NewScanner(strings.NewReader(src)), "h1", "/etc/crontab", "root", true)
	if len(jobs) != 3 {
		t.Fatalf("want 3 jobs, got %d", len(jobs))
	}
	if jobs[0].User != "root" || jobs[0].Schedule != "0 2 * * *" || jobs[0].Name != "backup.sh" {
		t.Errorf("bad job0: %+v", jobs[0])
	}
	if jobs[1].Schedule != "0 0 * * *" || jobs[1].User != "backup" {
		t.Errorf("bad @daily: %+v", jobs[1])
	}
	if jobs[2].Name != "artisan" {
		t.Errorf("name: %s", jobs[2].Name)
	}
}

func TestParseUserCrontab(t *testing.T) {
	jobs := parseCrontab(bufio.NewScanner(strings.NewReader("30 3 * * 0 /home/le/cleanup.sh")), "h1", "/var/spool/cron/crontabs/le", "le", false)
	if len(jobs) != 1 || jobs[0].User != "le" || jobs[0].Command != "/home/le/cleanup.sh" {
		t.Fatalf("bad: %+v", jobs)
	}
}

func TestFingerprintStable(t *testing.T) {
	a := Fingerprint("h", "root", "  /bin/x   --y ", "0 * * * *")
	b := Fingerprint("h", "root", "/bin/x --y", "0 * * * *")
	if a != b {
		t.Error("whitespace should not change fingerprint")
	}
	if a == Fingerprint("h", "root", "/bin/x --z", "0 * * * *") {
		t.Error("command change must change fingerprint")
	}
}

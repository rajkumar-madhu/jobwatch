// Package hostmetrics reads /proc and statfs — no external deps.
package hostmetrics

import (
	"bufio"
	"os"
	"strconv"
	"strings"
	"syscall"
	"time"
)

type Snapshot struct {
	TS        time.Time `json:"ts"`
	CPUPct    float64   `json:"cpu_pct"`
	MemPct    float64   `json:"mem_pct"`
	Load1     float64   `json:"load1"`
	DiskPct   float64   `json:"disk_pct"`
	InodePct  float64   `json:"inode_pct"`
	IOWaitPct float64   `json:"io_wait_pct"`
}

var lastTotal, lastIdle, lastIOWait uint64

func cpuTicks() (total, idle, iowait uint64) {
	f, err := os.Open("/proc/stat")
	if err != nil {
		return
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	if sc.Scan() {
		fs := strings.Fields(sc.Text())
		for i := 1; i < len(fs) && i <= 8; i++ {
			v, _ := strconv.ParseUint(fs[i], 10, 64)
			total += v
			if i == 4 {
				idle = v
			}
			if i == 5 {
				iowait = v
			}
		}
	}
	return
}

func meminfo() (total, avail uint64) {
	f, err := os.Open("/proc/meminfo")
	if err != nil {
		return
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		fs := strings.Fields(sc.Text())
		if len(fs) < 2 {
			continue
		}
		v, _ := strconv.ParseUint(fs[1], 10, 64)
		switch fs[0] {
		case "MemTotal:":
			total = v
		case "MemAvailable:":
			avail = v
		}
	}
	return
}

func Collect(mount string) Snapshot {
	s := Snapshot{TS: time.Now().UTC()}
	t, i, w := cpuTicks()
	if lastTotal > 0 && t > lastTotal {
		d := float64(t - lastTotal)
		s.CPUPct = 100 * (1 - float64(i-lastIdle)/d)
		s.IOWaitPct = 100 * float64(w-lastIOWait) / d
	}
	lastTotal, lastIdle, lastIOWait = t, i, w
	if mt, ma := meminfo(); mt > 0 {
		s.MemPct = 100 * (1 - float64(ma)/float64(mt))
	}
	if b, err := os.ReadFile("/proc/loadavg"); err == nil {
		if fs := strings.Fields(string(b)); len(fs) > 0 {
			s.Load1, _ = strconv.ParseFloat(fs[0], 64)
		}
	}
	var st syscall.Statfs_t
	if err := syscall.Statfs(mount, &st); err == nil && st.Blocks > 0 {
		s.DiskPct = 100 * (1 - float64(st.Bavail)/float64(st.Blocks))
		if st.Files > 0 {
			s.InodePct = 100 * (1 - float64(st.Ffree)/float64(st.Files))
		}
	}
	return s
}

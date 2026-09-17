// Package buffer is a crash-safe append-only JSONL queue with a committed offset file.
// Chosen over SQLite to keep the agent cgo-free and dependency-free.
package buffer

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"sync"
)

type Buffer struct {
	mu     sync.Mutex
	path   string
	f      *os.File
	offset int64 // bytes already acked
}

func Open(path string) (*Buffer, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR|os.O_APPEND, 0o640)
	if err != nil {
		return nil, err
	}
	b := &Buffer{path: path, f: f}
	if raw, err := os.ReadFile(path + ".offset"); err == nil {
		b.offset, _ = strconv.ParseInt(string(raw), 10, 64)
	}
	return b, nil
}

func (b *Buffer) Push(v any) error {
	b.mu.Lock()
	defer b.mu.Unlock()
	line, err := json.Marshal(v)
	if err != nil {
		return err
	}
	_, err = b.f.Write(append(line, '\n'))
	return err
}

// Peek returns up to n unacked raw lines and the byte offset after the last one.
func (b *Buffer) Peek(n int) ([]json.RawMessage, int64, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	rf, err := os.Open(b.path)
	if err != nil {
		return nil, b.offset, err
	}
	defer rf.Close()
	if _, err := rf.Seek(b.offset, 0); err != nil {
		return nil, b.offset, err
	}
	sc := bufio.NewScanner(rf)
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	var out []json.RawMessage
	pos := b.offset
	for len(out) < n && sc.Scan() {
		line := sc.Bytes()
		pos += int64(len(line)) + 1
		if len(line) == 0 {
			continue
		}
		out = append(out, append(json.RawMessage{}, line...))
	}
	return out, pos, sc.Err()
}

func (b *Buffer) Ack(newOffset int64) error {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.offset = newOffset
	if err := os.WriteFile(b.path+".offset", []byte(strconv.FormatInt(newOffset, 10)), 0o640); err != nil {
		return err
	}
	// compact when everything is acked and file > 8MB
	if st, err := b.f.Stat(); err == nil && st.Size() == newOffset && st.Size() > 8<<20 {
		b.f.Close()
		os.Truncate(b.path, 0)
		b.f, _ = os.OpenFile(b.path, os.O_CREATE|os.O_RDWR|os.O_APPEND, 0o640)
		b.offset = 0
		os.WriteFile(b.path+".offset", []byte("0"), 0o640)
	}
	return nil
}

func (b *Buffer) Close() error { return b.f.Close() }

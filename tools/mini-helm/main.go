// mini-helm: a from-scratch renderer for exactly the subset of Helm's template engine that
// infra/helm/{cronsentinel,cronsentinel-agent} actually use. Built because the real `helm` binary
// is unreachable in this sandbox — it transitively depends on k8s.io/client-go, k8s.io/api etc.,
// the same vanity-import wall that blocks agents/k8s-agent, and helm.sh/get.helm.sh are not on the
// egress allowlist either. Helm's rendering core IS text/template + Masterminds/sprig plus a
// handful of its own functions (include, toYaml, required, tpl) and four built-in objects
// (Values, Release, Chart, Files) — none of that needs k8s.io. This reimplements that surface
// faithfully enough to catch real template bugs (undefined values, bad indentation, malformed
// YAML, missing required fields) even though it is not a substitute for `helm install` itself.
package main

import (
	"bytes"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"text/template"

	"github.com/Masterminds/sprig/v3"
	"github.com/goccy/go-yaml"
)

type chartYAML struct {
	Name       string `yaml:"name"`
	Version    string `yaml:"version"`
	AppVersion string `yaml:"appVersion"`
}

type renderCtx struct {
	Values  map[string]any
	Release map[string]any
	Chart   map[string]any
}

// stringSlice implements flag.Value so --set can be repeated (flag.String can't be).
type stringSlice []string

func (s *stringSlice) String() string { return strings.Join(*s, ",") }
func (s *stringSlice) Set(v string) error {
	*s = append(*s, v)
	return nil
}

func main() {
	dir := flag.String("chart", "", "chart directory (containing Chart.yaml, values.yaml, templates/)")
	releaseName := flag.String("release", "release-name", "Release.Name")
	namespace := flag.String("namespace", "default", "Release.Namespace")
	var setFlags stringSlice
	flag.Var(&setFlags, "set", "key=value override of a top-level scalar in values.yaml; repeatable")
	flag.Parse()
	if *dir == "" {
		fmt.Fprintln(os.Stderr, "usage: mini-helm --chart <dir> [--set key=value ...]")
		os.Exit(2)
	}

	chartBytes, err := os.ReadFile(filepath.Join(*dir, "Chart.yaml"))
	must(err, "reading Chart.yaml")
	var ch chartYAML
	must(yaml.Unmarshal(chartBytes, &ch), "parsing Chart.yaml")

	valuesBytes, err := os.ReadFile(filepath.Join(*dir, "values.yaml"))
	must(err, "reading values.yaml")
	var values map[string]any
	must(yaml.Unmarshal(valuesBytes, &values), "parsing values.yaml")
	// --set only reaches top-level scalar keys (e.g. bootstrapToken) — the real `helm --set`
	// supports dotted paths and lists; that generality isn't needed by anything in this repo yet,
	// and a partial reimplementation that silently no-ops on a dotted path would be worse than
	// not having the flag at all, so this fails loudly on anything it can't actually apply.
	for _, kv := range setFlags {
		parts := strings.SplitN(kv, "=", 2)
		if len(parts) != 2 {
			fmt.Fprintf(os.Stderr, "--set %q: expected key=value\n", kv)
			os.Exit(2)
		}
		if strings.Contains(parts[0], ".") {
			fmt.Fprintf(os.Stderr, "--set %q: dotted paths are not supported, only top-level keys\n", kv)
			os.Exit(2)
		}
		values[parts[0]] = parts[1]
	}

	ctx := renderCtx{
		Values:  values,
		Release: map[string]any{"Name": *releaseName, "Namespace": *namespace, "Service": "Helm"},
		Chart:   map[string]any{"Name": ch.Name, "Version": ch.Version, "AppVersion": ch.AppVersion},
	}

	tplDir := filepath.Join(*dir, "templates")
	entries, err := os.ReadDir(tplDir)
	must(err, "reading templates/")

	// Two-pass load, matching Helm: every template (including _helpers.tpl, which defines named
	// templates via {{ define }} and renders nothing itself) is parsed into ONE template set
	// before any of them execute, so {{ include "chart.fullname" . }} across files resolves.
	var root *template.Template
	root = template.New("root").Funcs(sprig.TxtFuncMap()).Funcs(template.FuncMap{
		"toYaml": func(v any) string {
			b, err := yaml.Marshal(v)
			must(err, "toYaml")
			return strings.TrimRight(string(b), "\n")
		},
		"required": func(msg string, v any) (any, error) {
			if v == nil || v == "" {
				return nil, fmt.Errorf("required value not set: %s", msg)
			}
			return v, nil
		},
		"include": func(name string, data any) (string, error) {
			var buf bytes.Buffer
			if err := root.ExecuteTemplate(&buf, name, data); err != nil {
				return "", err
			}
			return buf.String(), nil
		},
	})

	var names []string
	for _, e := range entries {
		if e.IsDir() || (!strings.HasSuffix(e.Name(), ".yaml") && !strings.HasSuffix(e.Name(), ".tpl")) {
			continue
		}
		b, err := os.ReadFile(filepath.Join(tplDir, e.Name()))
		must(err, "reading "+e.Name())
		t := root.New(e.Name())
		_, err = t.Parse(string(b))
		must(err, "parsing "+e.Name())
		if strings.HasSuffix(e.Name(), ".yaml") {
			names = append(names, e.Name())
		}
	}

	failures := 0
	for _, name := range names {
		var buf bytes.Buffer
		if err := root.ExecuteTemplate(&buf, name, ctx); err != nil {
			fmt.Printf("--- %s: TEMPLATE ERROR ---\n%v\n", name, err)
			failures++
			continue
		}
		out := buf.String()
		fmt.Printf("--- # Source: %s\n%s\n", name, out)
		// A template producing multiple `---`-separated documents (Helm allows this from one
		// file) must yield valid YAML for every document, and every document that looks like a
		// Kubernetes object must actually have apiVersion/kind/metadata.name — the three fields
		// every one of these charts' own resources needs and that a bad template most often drops.
		for i, doc := range strings.Split(out, "\n---\n") {
			doc = strings.TrimSpace(doc)
			if doc == "" {
				continue
			}
			var parsed map[string]any
			if err := yaml.Unmarshal([]byte(doc), &parsed); err != nil {
				fmt.Printf("    doc %d: INVALID YAML: %v\n", i, err)
				failures++
				continue
			}
			if parsed == nil {
				continue
			}
			for _, req := range []string{"apiVersion", "kind"} {
				if _, ok := parsed[req]; !ok {
					fmt.Printf("    doc %d: missing required field %q\n", i, req)
					failures++
				}
			}
			if md, ok := parsed["metadata"].(map[string]any); ok {
				if n, ok := md["name"]; !ok || n == "" || n == nil {
					fmt.Printf("    doc %d (%v): metadata.name is empty\n", i, parsed["kind"])
					failures++
				}
				// R37: caught a real chart bug this way — an unquoted comma inside a flow-style
				// annotations map (`{"helm.sh/hook": pre-install,pre-upgrade}`) splits into two
				// map entries, the second with a null value. Kubernetes annotations/labels are
				// map[string]string; the real API server rejects a null value outright, so this
				// generic check (not specific to the hook bug) catches the whole class.
				for _, field := range []string{"annotations", "labels"} {
					m, ok := md[field].(map[string]any)
					if !ok {
						continue
					}
					for k, v := range m {
						if v == nil {
							fmt.Printf("    doc %d (%v): metadata.%s[%q] is null — almost always an unquoted comma or colon inside a flow-style {..} map splitting one entry into two\n", i, parsed["kind"], field, k)
							failures++
						}
					}
				}
			} else {
				fmt.Printf("    doc %d (%v): no metadata block\n", i, parsed["kind"])
				failures++
			}
		}
	}
	if failures > 0 {
		fmt.Fprintf(os.Stderr, "\n%d issue(s) found\n", failures)
		os.Exit(1)
	}
	fmt.Fprintln(os.Stderr, "\nall templates rendered to valid, well-formed Kubernetes YAML")
}

func must(err error, ctx string) {
	if err != nil {
		fmt.Fprintf(os.Stderr, "%s: %v\n", ctx, err)
		os.Exit(1)
	}
}

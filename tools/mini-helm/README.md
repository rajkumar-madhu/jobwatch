# mini-helm

A from-scratch renderer for exactly the subset of Helm's template engine that
`infra/helm/{cronsentinel,cronsentinel-agent}` actually use.

## Why this exists instead of just running `helm template`

The real `helm` binary can't be built or installed in the environment this repo has been
developed in: it transitively depends on `k8s.io/client-go`, `k8s.io/api`, etc., and `helm.sh` /
`get.helm.sh` are not reachable either. That's the same wall that blocks `agents/k8s-agent` (see
`docs/DEVELOPMENT.md` R35/R37).

Helm's rendering core is not actually k8s-specific — it's Go's `text/template`, the
`Masterminds/sprig` function library, and a handful of Helm's own functions (`include`, `toYaml`,
`required`) plus four built-in objects (`.Values`, `.Release`, `.Chart`, `.Files`). None of that
needs `k8s.io`. This reimplements that surface — enough to catch real template bugs (undefined
values, bad indentation, malformed YAML, a `required()` guard that doesn't actually fire) — without
being a full substitute for `helm install`/`helm lint`/JSON-schema validation against a live
cluster's API.

**Found by this tool, first run, before it ever shipped (R37):** an unquoted comma inside a
flow-style `annotations: {"helm.sh/hook": pre-install,pre-upgrade, ...}` map silently split into
two entries — `"helm.sh/hook": "pre-install"` plus a bogus null-valued `"pre-upgrade"` key. Real
effect: the schema-migration Job only ever ran on the *first* `helm install`; every `helm upgrade`
after that silently skipped `alembic upgrade head`. See the `R37` comments in
`infra/helm/cronsentinel/templates/{secret,workloads}.yaml` for the fix.

## What it checks

For every `.yaml` file under a chart's `templates/`:
1. The template must parse and execute (undefined values, bad `range`/`if`, a failing
   `required()` call, etc. are template errors, reported per-file).
2. Every `---`-separated document in the output must be valid YAML.
3. Every document must have `apiVersion` and `kind`, and a non-empty `metadata.name`.
4. No key inside `metadata.annotations` or `metadata.labels` may have a `null` value — this is
   always the result of an unquoted comma or colon splitting one flow-map entry into two, and is
   always rejected by a real Kubernetes API server (annotations/labels are `map[string]string`).

## Usage

```
go build -o mini-helm .
./mini-helm --chart ../../infra/helm/cronsentinel --release myrelease --namespace jobwatch
./mini-helm --chart ../../infra/helm/cronsentinel-agent --release myagent --namespace jobwatch \
  --set bootstrapToken=<real-token>
```

`--set key=value` only reaches **top-level** scalar keys in `values.yaml` (e.g. `bootstrapToken`).
It does not support dotted paths or lists the way real `helm --set` does — that generality isn't
needed by anything in this repo yet, and it fails loudly (exit 2) on a dotted path rather than
silently no-opping, since a partial reimplementation that looks like it worked would be worse than
not having the flag.

## What this is NOT

- Not a substitute for `helm lint`, `helm template --validate` against a live API server's OpenAPI
  schema, or an actual `helm install` on a real cluster. It cannot catch a field that's spelled
  correctly as YAML but is not a valid field for that Kubernetes API version/kind — it never
  fetches a schema.
- `.Files` (`{{ .Files.Get ... }}`) is not implemented; neither chart uses it.
- Subcharts / `.Values.<dependency>` merging is not implemented; neither chart has dependencies.
- CI (`.github/workflows/ci.yml`, `helm` job) builds this from the committed `go.sum` with
  `GOPROXY` unset to its default — no special network access is needed at build or run time.

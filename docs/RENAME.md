# Rename: CronSentinel → WeCrew JobWatch

Commit `chore(rename)` changes **display strings only** — no logic, no identifiers.

## Changed
Product name in UI copy, page titles, landing/onboarding/status pages, email + Slack message headers, docs, Helm chart descriptions, agent `User-Agent` (`JobWatch/<ver>`).

## Deliberately NOT changed (would be logic/infra changes, not a rename)
| Identifier | Value | Why |
|---|---|---|
| Python package | `cronsentinel` | import paths across api/workers/tests; rename = separate commit + Dockerfile/Helm command updates |
| Go modules | `github.com/cronsentinel/{linux,k8s}-agent` | module path change breaks existing checkouts; do with the repo move |
| Agent binary / config | `cronsentinel-agent`, `/etc/cronsentinel/`, `/var/lib/cronsentinel/` | installed hosts would need migration; needs an upgrade path in the installer |
| Helm releases / K8s names | `cronsentinel`, `cronsentinel-agent` | renaming forces uninstall+reinstall on existing clusters |
| DB objects, env var prefixes (`CS_`, `COPILOT_`), heartbeat token prefixes (`cs_`, `csa_`, `csb_`) | unchanged | data-compatibility |
| Cosmetic CSS class `csa.name`, `cs.fullname` | unchanged | template-internal |

## When identifiers are renamed later
Order: (1) Python package with a `cronsentinel` shim module, (2) Go module paths + new binary name with a `cs-run` symlink kept, (3) agent config path with fallback read of the old path for one release, (4) Helm chart rename as a new chart name with a documented migration.

# cronsentinel-agent Helm chart
```bash
helm install cronsentinel-agent ./infra/helm/cronsentinel-agent -n cronsentinel --create-namespace \
  --set server=https://ingest.example.com --set clusterName=production --set bootstrapToken=csb_...
# namespace-scoped:
#   --set scope=namespaces --set 'namespaces={payments,analytics}'
```
After first start, copy `agent_id`/`agent_key` from pod logs into a Secret and set `existingSecret` so restarts don't need a new bootstrap token.
Read-only RBAC: get/list/watch on cronjobs, jobs, pods, events, nodes.

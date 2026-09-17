#!/usr/bin/env bash
# curl -fsSL https://<server>/install.sh | sudo bash -s -- --server https://ingest.example.com --token <bootstrap>
set -euo pipefail
SERVER=""; TOKEN=""; VERSION="${CS_AGENT_VERSION:-0.1.0}"; BASE="${CS_DOWNLOAD_BASE:-https://downloads.example.com/agent}"
while [[ $# -gt 0 ]]; do case "$1" in --server) SERVER="$2"; shift 2;; --token) TOKEN="$2"; shift 2;; *) shift;; esac; done
[[ -z "$SERVER" || -z "$TOKEN" ]] && { echo "usage: install.sh --server URL --token TOKEN"; exit 1; }
[[ $EUID -ne 0 ]] && { echo "run as root"; exit 1; }
ARCH=$(uname -m); case "$ARCH" in x86_64) ARCH=amd64;; aarch64) ARCH=arm64;; esac
curl -fsSL "$BASE/$VERSION/cronsentinel-agent-linux-$ARCH" -o /usr/local/bin/cronsentinel-agent
chmod 0755 /usr/local/bin/cronsentinel-agent
ln -sf /usr/local/bin/cronsentinel-agent /usr/local/bin/cs-run   # `cs-run` alias → `cronsentinel-agent exec`
mkdir -p /var/lib/cronsentinel /etc/cronsentinel
/usr/local/bin/cronsentinel-agent enroll --server "$SERVER" --token "$TOKEN"
curl -fsSL "$BASE/$VERSION/cronsentinel-agent.service" -o /etc/systemd/system/cronsentinel-agent.service
systemctl daemon-reload && systemctl enable --now cronsentinel-agent
echo "CronSentinel agent installed. Wrap jobs with: cs-run -- /path/to/script.sh"

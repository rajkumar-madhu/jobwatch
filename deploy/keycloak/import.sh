#!/usr/bin/env bash
# R35 — one-shot import of cronsentinel-realm.json, for the Keycloak deploy that was always a
# stubbed issuer URL (config.py's keycloak_issuer default) with nothing behind it.
#
# What this does and does not set up:
#   - Creates (or updates, if already there) the "cronsentinel" realm and its one client.
#   - Rotates the client secret and prints it — put it straight into KEYCLOAK_CLIENT_SECRET.
#   - Does NOT create any users, groups, or roles. JobWatch does not read them: every
#     authorization decision comes from the app's own `memberships` table (see
#     cronsentinel/routers/auth.py — the callback looks up org_id/role by email, never by a
#     Keycloak claim). Keycloak's only job here is proving identity (sub, email, name); teach
#     people to sign up through the app's own /auth/signup, not through a Keycloak admin console.
#
# Usage:
#   KEYCLOAK_URL=https://sso.example.com \
#   KEYCLOAK_ADMIN_PASSWORD=... \
#   API_PUBLIC_URL=https://api.jobwatch.example.com \
#   WEB_PUBLIC_URL=https://app.jobwatch.example.com \
#   ./import.sh
#
# Requires kcadm.sh on PATH (ships inside every Keycloak container at
# /opt/keycloak/bin/kcadm.sh — mount or exec into it if running via compose/Helm).
set -euo pipefail
cd "$(dirname "$0")"

: "${KEYCLOAK_URL:?e.g. https://sso.example.com (no trailing slash)}"
: "${KEYCLOAK_ADMIN_PASSWORD:?the realm-master admin password}"
KEYCLOAK_ADMIN_USER=${KEYCLOAK_ADMIN_USER:-admin}
API_PUBLIC_URL=${API_PUBLIC_URL:-http://localhost:8000}
WEB_PUBLIC_URL=${WEB_PUBLIC_URL:-http://localhost:3000}
KCADM=${KCADM:-kcadm.sh}

command -v "$KCADM" >/dev/null || { echo "kcadm.sh not found — set KCADM=/path/to/kcadm.sh"; exit 1; }

echo "== rendering realm export with real redirect/logout URIs =="
REALM_JSON=$(mktemp)
trap 'rm -f "$REALM_JSON"' EXIT
python3 - "$API_PUBLIC_URL" "$WEB_PUBLIC_URL" <<'PY' > "$REALM_JSON"
import json, sys
api, web = sys.argv[1], sys.argv[2]
doc = json.load(open("cronsentinel-realm.json"))
doc["clients"][0]["redirectUris"] = [f"{api}/auth/callback"]
doc["clients"][0]["attributes"]["post.logout.redirect.uris"] = web
# a self-hosted deploy behind its own TLS terminator can safely stay http internally; only
# require external TLS when the public URL itself is https, so local/dev imports still work.
doc["sslRequired"] = "external" if api.startswith("https://") else "none"
print(json.dumps(doc, indent=2))
PY

echo "== authenticating to $KEYCLOAK_URL =="
"$KCADM" config credentials --server "$KEYCLOAK_URL" --realm master --user "$KEYCLOAK_ADMIN_USER" --password "$KEYCLOAK_ADMIN_PASSWORD"

if "$KCADM" get realms/cronsentinel >/dev/null 2>&1; then
  echo "== realm exists — updating in place (create-or-update, not destructive) =="
  "$KCADM" update realms/cronsentinel -f "$REALM_JSON"
  CID=$("$KCADM" get clients -r cronsentinel -q clientId=cronsentinel-web --fields id --format csv --noquotes | tail -1)
  "$KCADM" update "clients/$CID" -r cronsentinel -f - <<< "$(python3 -c "import json;print(json.dumps(json.load(open('$REALM_JSON'))['clients'][0]))")"
else
  echo "== creating realm =="
  "$KCADM" create realms -f "$REALM_JSON"
  CID=$("$KCADM" get clients -r cronsentinel -q clientId=cronsentinel-web --fields id --format csv --noquotes | tail -1)
fi

echo "== rotating client secret =="
SECRET=$("$KCADM" get "clients/$CID/client-secret" -r cronsentinel --fields value --format csv --noquotes 2>/dev/null || true)
if [[ -z "$SECRET" ]]; then
  "$KCADM" create "clients/$CID/client-secret" -r cronsentinel >/dev/null
  SECRET=$("$KCADM" get "clients/$CID/client-secret" -r cronsentinel --fields value --format csv --noquotes)
fi

cat <<EOF

== done ==
Set these on the API deployment:
  KEYCLOAK_ISSUER=$KEYCLOAK_URL/realms/cronsentinel
  KEYCLOAK_CLIENT_ID=cronsentinel-web
  KEYCLOAK_CLIENT_SECRET=$SECRET
  API_PUBLIC_URL=$API_PUBLIC_URL
  WEB_PUBLIC_URL=$WEB_PUBLIC_URL

No users, groups or roles were created. First login for each person still goes through the app's
own /auth/login → Keycloak → /auth/callback → the app's own org signup/invite flow — Keycloak only
has to know their email exists (or let them register one, if you enable that in the realm).
EOF

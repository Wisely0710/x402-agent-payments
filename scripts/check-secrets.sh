#!/usr/bin/env bash
# Secret scanner for tracked files — zero dependencies, offline, no values echoed.
#
# Usage:
#   scripts/check-secrets.sh            # scan every tracked file (CI)
#   scripts/check-secrets.sh --staged   # scan staged content (pre-commit hook)
#
# Prints one line per hit as "path:line  [pattern]" and never prints the matched
# value. Exits non-zero when anything matched, so it works as a gate.
#
# Allowlist: scripts/secret-scan-allow.txt — one extended regex per line
# (comments with "#"), matched against the raw "path:line:content" hit. Use it
# for known-public values (e.g. the well-known Anvil/Hardhat dev keys) and for
# false-positive shapes.
set -euo pipefail

MODE=all
case "${1:-}" in
  "" | --all) MODE=all ;;
  --staged) MODE=staged ;;
  *)
    echo "usage: $0 [--all|--staged]" >&2
    exit 2
    ;;
esac

root="$(git rev-parse --show-toplevel)"
cd "$root"

ALLOW_FILE="scripts/secret-scan-allow.txt"

# name|extended-regex
PATTERNS='
private-key-block|-----BEGIN [A-Z ]*PRIVATE KEY-----
aws-access-key-id|AKIA[0-9A-Z]{16}
github-token|(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})
slack-token|xox[abprs]-[A-Za-z0-9-]{10,}
openai-style-key|sk-[A-Za-z0-9_-]{28,}
jwt|eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}
url-with-password|(postgres|postgresql|mysql|mongodb)(\+[a-z]+)?://[^:/[:space:]]+:[^@[:space:]]{3,}@
url-with-basic-auth|https?://[^:/[:space:]@]+:[^@[:space:]]{6,}@
private-key-assignment|(private_key|PRIVATE_KEY|secret_key|SECRET_KEY)["'"'"']?[[:space:]]*[:=][[:space:]]*["'"'"']?(0x)?[0-9a-fA-F]{64}
'

if [ "$MODE" = staged ]; then
  grep_scope=(--cached)
else
  grep_scope=()
fi

allowlist=()
if [ -s "$ALLOW_FILE" ]; then
  while IFS= read -r line; do
    case "$line" in '' | '#'*) continue ;; esac
    allowlist+=("$line")
  done < "$ALLOW_FILE"
fi

is_allowed() {
  local hit="$1" pattern
  for pattern in "${allowlist[@]:-}"; do
    [ -n "$pattern" ] || continue
    if printf '%s\n' "$hit" | grep -qE -- "$pattern"; then
      return 0
    fi
  done
  return 1
}

failures=0

# 1) Tracked files whose name itself is secret-bearing (keystores, real env files).
while IFS= read -r path; do
  [ -n "$path" ] || continue
  echo "TRACKED SECRET FILE  $path"
  failures=$((failures + 1))
done < <(
  git ls-files \
    | grep -E '(^|/)(\.env|\.env\.[^/]+|id_(rsa|dsa|ecdsa|ed25519)|[^/]*\.(pem|p12|pfx|jks|keystore))$' \
    | grep -vE '\.(example|sample|template)$' || true
)

# 2) Secret-shaped content.
while IFS='|' read -r name ere; do
  [ -n "$name" ] || continue
  hits="$(git grep -nIE "${grep_scope[@]+"${grep_scope[@]}"}" -e "$ere" -- . ":!${ALLOW_FILE}" || true)"
  [ -n "$hits" ] || continue
  while IFS= read -r hit; do
    [ -n "$hit" ] || continue
    is_allowed "$hit" && continue
    # keep "path:line" only — never the matched content
    printf '%s  [%s]\n' "$(printf '%s' "$hit" | sed -E 's/^([^:]+:[0-9]+):.*/\1/')" "$name"
    failures=$((failures + 1))
  done <<< "$hits"
done <<< "$PATTERNS"

if [ "$failures" -gt 0 ]; then
  echo ""
  echo "check-secrets: ${failures} hit(s); remove the value, rotate it, or allowlist it with a comment in ${ALLOW_FILE}."
  exit 1
fi

echo "check-secrets: OK (mode=${MODE})"

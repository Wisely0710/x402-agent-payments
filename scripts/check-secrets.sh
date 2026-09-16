#!/usr/bin/env bash
# Secret scanner for tracked files — zero dependencies, offline, no values echoed.
#
# Usage:
#   scripts/check-secrets.sh            # scan every tracked file (CI)
#   scripts/check-secrets.sh --staged   # scan staged content (pre-commit hook)
#   scripts/check-secrets.sh --history  # scan every commit reachable from all refs
#                                       # (CI; needs a full clone: fetch-depth: 0)
#
# Prints one line per hit as "path:line  [pattern]" and never prints the matched
# value. Exits non-zero when anything matched, so it works as a gate.
#
# --history covers what the other modes cannot: a value that was committed and
# deleted later is still in the repository, so it is still a leak. Historical
# hits are reported once per location+pattern, with the earliest matching commit.
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
  --history) MODE=history ;;
  *)
    echo "usage: $0 [--all|--staged|--history]" >&2
    exit 2
    ;;
esac

root="$(git rev-parse --show-toplevel)"
cd "$root"

ALLOW_FILE="scripts/secret-scan-allow.txt"

# Paths whose name itself is secret-bearing (keystores, real env files).
SECRET_PATH_RE='(^|/)(\.env|\.env\.[^/]+|id_(rsa|dsa|ecdsa|ed25519)|[^/]*\.(pem|p12|pfx|jks|keystore))$'

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

# "path:line:content" -> "path:line"; the content never reaches stdout.
hit_location() {
  printf '%s' "$1" | sed -E 's/^([^:]+:[0-9]+):.*/\1/'
}

failures=0

if [ "$MODE" = history ]; then
  # 1) Paths that ever entered the history: a .env committed once and deleted
  #    later is still in the repository.
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    echo "HISTORICAL SECRET FILE  $path"
    failures=$((failures + 1))
  done < <(
    git log --all --pretty=format: --name-only --diff-filter=A \
      | grep -E "$SECRET_PATH_RE" \
      | grep -vE '\.(example|sample|template)$' \
      | sort -u || true
  )

  # 2) Every commit tree, oldest first. One git grep per commit (all patterns in
  #    one pass); matches are deduplicated by location+pattern.
  pattern_args=()
  while IFS='|' read -r name ere; do
    [ -n "$name" ] || continue
    pattern_args+=(-e "$ere")
  done <<< "$PATTERNS"

  matches="$(mktemp)"
  trap 'rm -f "$matches"' EXIT

  while IFS= read -r commit; do
    hits="$(git grep -nIE "${pattern_args[@]}" "$commit" -- . ":!${ALLOW_FILE}" || true)"
    [ -n "$hits" ] || continue
    while IFS= read -r hit; do
      [ -n "$hit" ] || continue
      # git grep prints "<tree-ish>:<path>:<line>:<content>" for a commit
      hit="${hit#"$commit":}"
      is_allowed "$hit" && continue
      location="$(hit_location "$hit")"
      while IFS='|' read -r name ere; do
        [ -n "$name" ] || continue
        printf '%s' "$hit" | grep -qE -- "$ere" || continue
        printf '%s|%s|%s\n' "$location" "$name" "$commit" >> "$matches"
      done <<< "$PATTERNS"
    done <<< "$hits"
  done < <(git rev-list --reverse --all)

  while IFS='|' read -r location name commit; do
    [ -n "$location" ] || continue
    printf '%s  [%s]  (first match in %s)\n' "$location" "$name" "${commit:0:12}"
    failures=$((failures + 1))
  done < <(awk -F'|' '!seen[$1 FS $2]++' "$matches")
else
  if [ "$MODE" = staged ]; then
    grep_scope=(--cached)
  else
    grep_scope=()
  fi

  # 1) Tracked files whose name itself is secret-bearing (keystores, real env files).
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    echo "TRACKED SECRET FILE  $path"
    failures=$((failures + 1))
  done < <(
    git ls-files \
      | grep -E "$SECRET_PATH_RE" \
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
      printf '%s  [%s]\n' "$(hit_location "$hit")" "$name"
      failures=$((failures + 1))
    done <<< "$hits"
  done <<< "$PATTERNS"
fi

if [ "$failures" -gt 0 ]; then
  echo ""
  echo "check-secrets: ${failures} hit(s); remove the value, rotate it, or allowlist it with a comment in ${ALLOW_FILE}."
  exit 1
fi

echo "check-secrets: OK (mode=${MODE})"

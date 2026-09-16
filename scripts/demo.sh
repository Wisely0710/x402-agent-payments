#!/usr/bin/env bash
# One-command demo: an AI agent pays for an x402-protected resource.
#
#   scripts/demo.sh
#
# No chain, no facilitator, no real key, no external service. On first run it
# creates .demo/venv with the two packages the demo actually needs (the x402 SDK
# and eth-account — not the MCP SDK), which takes seconds.
set -euo pipefail

root="$(git rev-parse --show-toplevel)"
cd "$root"

venv="${DEMO_VENV:-$root/.demo/venv}"

if [ ! -x "$venv/bin/python" ]; then
  python_bin="${DEMO_PYTHON:-}"
  if [ -z "$python_bin" ]; then
    for candidate in python3.12 python3.11 python3; do
      if command -v "$candidate" >/dev/null 2>&1; then
        python_bin="$candidate"
        break
      fi
    done
  fi
  if [ -z "$python_bin" ]; then
    echo "demo: need python 3.11+ (set DEMO_PYTHON to override)" >&2
    exit 2
  fi
  echo "demo: creating $venv with $python_bin"
  "$python_bin" -m venv "$venv"
  "$venv/bin/pip" install --quiet --disable-pip-version-check \
    -r "$root/scripts/demo-requirements.txt"
fi

# The demo imports the two components straight from the checkout: the verifier
# (`x402_v2`) and the buying client (`x402_mcp`).
PYTHONPATH="$root/verifier:$root/mcp/src" exec "$venv/bin/python" \
  "$root/mcp/examples/demo_paid_flow.py" "$@"

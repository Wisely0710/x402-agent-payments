#!/usr/bin/env bash
# Install the committed git hooks so a fresh clone gets them with one command:
#   scripts/install-hooks.sh
# (.githooks/pre-commit runs the zero-dependency secret scan on staged content.)
set -euo pipefail

root="$(git rev-parse --show-toplevel)"
cd "$root"

git config core.hooksPath .githooks
chmod +x .githooks/* scripts/check-secrets.sh
echo "core.hooksPath -> .githooks; pre-commit runs scripts/check-secrets.sh --staged"

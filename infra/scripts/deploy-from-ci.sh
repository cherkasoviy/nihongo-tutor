#!/usr/bin/env bash
# The only thing CI's deploy key can do.
#
# Installed as a forced command in the deploy user's authorized_keys, so a session opened with that
# key runs this and nothing else — no shell, no forwarding. The requested commit arrives in
# SSH_ORIGINAL_COMMAND, and it is the only input an attacker holding the key could vary. Everything
# else about the deploy is fixed on this side of the connection.
set -euo pipefail

SHA="${SSH_ORIGINAL_COMMAND:-}"

# Strict, and anchored. A loose pattern here would be a command-injection hole with a private key
# already in the attacker's hands: this string is about to be handed to git.
if [[ ! "$SHA" =~ ^[0-9a-f]{40}$ ]]; then
    echo "refusing: expected a 40-character commit sha, got '${SHA}'" >&2
    exit 2
fi

cd /opt/nihongo-tutor

git fetch origin main
git checkout -q main
# --ff-only is the substance of the guard. It deploys exactly the commit CI tested, and refuses
# anything that is not already an ancestor-or-equal of origin/main — so a valid-looking sha from a
# fork, a branch, or a rewritten history is rejected rather than checked out and run.
git merge --ff-only "$SHA"

# Without --no-pull, deploy.sh would pull main again and could land on something newer than the
# commit CI signed off.
exec infra/scripts/deploy.sh --no-pull

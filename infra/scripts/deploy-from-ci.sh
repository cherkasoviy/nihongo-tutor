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

# The real guard: the sha has to be on origin/main.
#
# --ff-only alone is not that. It only proves the sha descends from whatever this checkout happens
# to be on, and the object store here is not clean — every past manual deploy ran a bare `git pull`,
# whose default refspec drags every claude/** branch tip into it. So an unmerged feature branch that
# descends from main would satisfy --ff-only and be deployed without ever having been reviewed.
if ! git merge-base --is-ancestor "$SHA" origin/main; then
    echo "refusing: ${SHA} is not on origin/main" >&2
    exit 3
fi

git checkout -q main
# Belt to the check above's braces: this would also catch a local main that had somehow diverged.
git merge --ff-only "$SHA"

# Without --no-pull, deploy.sh would pull main again and could land on something newer than the
# commit CI signed off.
exec infra/scripts/deploy.sh --no-pull

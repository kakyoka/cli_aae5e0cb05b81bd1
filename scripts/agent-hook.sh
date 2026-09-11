#!/bin/sh
# minis-bridge/scripts/agent-hook.sh
# Default agent hook for bridge-dispatch.sh. Invoked with the request body
# on stdin (or as $1 in the dispatcher wrapper).
#
# Minis-side callers usually override this with a hook that forwards to
# the active Minis conversation. The default below writes a placeholder
# reply so the bridge is end-to-end testable without an agent attached.

body=${1:-$(cat)}

cat <<EOF
Minis-bridge hook placeholder.

The active Minis conversation should be running this dispatcher in a loop.
When the dispatcher calls the agent hook, it should forward the body to
the agent's "next user message" queue and wait for the agent's reply.

For the simplest setup, override MINIS_BRIDGE_HOOK with a script that:

  1. Reads the active Minis conversation id (from a config file or env)
  2. POSTs the body to the conversation's "send prompt" endpoint
  3. Polls until the agent produces a reply
  4. Prints the reply to stdout

See SKILL.md "Advanced: talking to Minis's debug server" for the endpoint
shape once the debug server is reachable over Tailscale.

Original request body (first 500 chars):
$(printf '%s' "$body" | head -c 500)
EOF
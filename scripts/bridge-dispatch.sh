#!/bin/sh
# minis-bridge/scripts/bridge-dispatch.sh
# Minis-side dispatcher: polls inbox/, runs each request through the
# Minis agent, writes the reply to outbox/, archives the request.
#
# Lives in /var/minis/skills/minis-bridge/scripts/ (after Minis-side install).
# Designed to be invoked by the Minis agent every 15-30 seconds; the agent
# itself owns the long-running loop and just calls this helper once per tick.

set -eu

QUEUE="${MINIS_BRIDGE_QUEUE:-/var/minis/mounts/iCloud/Hermes-Minis}"
INBOX="$QUEUE/inbox"
OUTBOX="$QUEUE/outbox"
PROCESSED="$QUEUE/processed"
LOG="${MINIS_BRIDGE_LOG:-/var/minis/memory/minis-bridge.log}"

mkdir -p "$INBOX" "$OUTBOX" "$PROCESSED"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() {
  printf '[%s] %s\n' "$(ts)" "$1" >> "$LOG"
}

log "dispatch tick"

# Find the oldest unprocessed request. -print0 / xargs -0 is the safe path
# because filenames from iCloud can contain spaces.
pending=$(
  find "$INBOX" -maxdepth 1 -type f -name '*.md' -printf '%T@ %p\0' 2>/dev/null \
    | sort -z -n \
    | head -z -n 1 \
    | cut -z -d' ' -f2-
)

if [ -z "$pending" ]; then
  exit 0
fi

log "dispatching $(basename "$pending")"

# Extract the body (everything after the closing --- of the frontmatter)
# Falls back to the whole file if no frontmatter.
body=$(
  awk '
    BEGIN { in_fm = 1 }
    in_fm && /^---$/ { c++; if (c == 2) { in_fm = 0; next } }
    !in_fm { print }
  ' "$pending"
)

if [ -z "$body" ]; then
  body=$(cat "$pending")
fi

# Pull id from frontmatter if present, otherwise derive from filename.
req_id=$(awk '
  BEGIN { in_fm = 1 }
  in_fm && /^---$/ { c++; if (c == 2) { in_fm = 0; exit } }
  in_fm && /^id:/ { sub(/^id:[[:space:]]*/, ""); print; exit }
' "$pending")

if [ -z "$req_id" ]; then
  req_id=$(basename "$pending" .md)
fi

# Run the request body through the Minis agent. The agent here is whatever
# the parent conversation has set up — typically an `apple-open`-style call
# into the active session, or a direct LLM call if the dispatcher is
# driven by a cron-style background task. We invoke a hook script so the
# Minis-side install can plug in the right transport.
hook="${MINIS_BRIDGE_HOOK:-/var/minis/skills/minis-bridge/scripts/agent-hook.sh}"

if [ -x "$hook" ]; then
  reply_body=$("$hook" "$body" 2>&1) || reply_body="ERROR: agent hook failed

$reply_body"
else
  reply_body="ERROR: no agent hook configured at $hook. Set MINIS_BRIDGE_HOOK or create the hook script."
fi

status="ok"
case "$reply_body" in
  ERROR:*) status="error" ;;
esac

# Write the reply.
out_name="${req_id}.md"
{
  printf -- '---\n'
  printf 'id: %s\n' "$req_id"
  printf 'from: minis-iphone\n'
  printf 'to: hermes-desktop\n'
  printf 'in_reply_to: %s\n' "$req_id"
  printf 'completed: %s\n' "$(ts)"
  printf 'status: %s\n' "$status"
  printf -- '---\n\n'
  printf '%s\n' "$reply_body"
} > "$OUTBOX/$out_name.tmp"

# Atomic-ish move into place.
mv "$OUTBOX/$out_name.tmp" "$OUTBOX/$out_name"

# Archive the request.
mv "$pending" "$PROCESSED/"

log "dispatched ok: $req_id ($status)"
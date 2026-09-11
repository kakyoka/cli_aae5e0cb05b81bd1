---
name: minis-bridge
version: 1.0.0
description: >
  Bidirectional message bridge between Hermes Desktop and Open Minis on iPhone,
  via an iCloud Drive message-queue directory. Trigger this skill whenever the
  user wants to push a task from a Hermes/Windows/Mac session to Minis on the
  iPhone and read the result back — e.g. "send this to Minis", "ask Minis to
  summarise X", "what did Minis say about Y". Reads inbox/ for new request
  files written by Hermes, dispatches them through the Minis agent runtime,
  and writes the reply into outbox/ for Hermes to pick up. Works without any
  custom HTTP server, MCP, or webhook — relies only on the iCloud Drive
  mount that Minis already exposes at /var/minis/mounts/iCloud/.
platforms: [ios]
compatibility: >
  iOS only. Minis 1.13+ required (iCloud Drive mount + iSH polling).
  No external dependencies beyond the iCloud Drive sync interval (typically
  5–30 seconds). The bridge is fully on-device: Minis reads and writes files
  locally, and iCloud Drive handles transport.
---

# Minis Bridge — Hermes ↔ Minis via iCloud Drive Queue

## What it does

```
┌────────────────────┐                ┌────────────────────┐
│  Hermes Desktop    │                │  Open Minis on     │
│  (Win / Mac / VPS) │                │  iPhone (iSH)      │
│                    │                │                    │
│  scripts/          │   iCloud Drive │  bridge daemon     │
│  minis_bridge.py   │ ─────────────► │  (this skill)      │
│                    │                │                    │
│  writes inbox/*.md │   inbox/  ──►  │  polls every 15 s  │
│  reads outbox/*.md │ ◄──  outbox/   │  writes result     │
└────────────────────┘                └────────────────────┘
```

The bridge is two cooperating scripts:

- **Hermes side** (`scripts/minis_bridge.py`) — lives in your Hermes skills
  folder. Writes request files to `iCloud Drive /Hermes-Minis/inbox/` and
  reads replies from `outbox/`.
- **Minis side** (this skill) — installed in Minis's `/var/minis/skills/`.
  A small shell loop polls `inbox/`, runs each request through the Minis
  agent runtime, and writes the result to `outbox/`.

Both sides only touch a directory they already have access to. No new
infrastructure, no HTTP server, no MCP, no webhook. The transport is
iCloud Drive sync, which Apple already encrypts end-to-end.

## File protocol

Every request and reply is a single Markdown file. Filenames carry the
correlation id so the Hermes script can match replies to requests.

### Request file (`inbox/<ts>-<topic>.md`)

```markdown
---
id: 2026-09-10T18:00:00Z-card-summary
from: hermes-desktop
to: minis-iphone
created: 2026-09-10T18:00:00Z
reply_to: /Hermes-Minis/outbox/
---

# Card summary

Summarise this Substack article in 3 bullet points and tell me whether the
author's thesis holds up under Munger's framework:

https://example.com/article

Use the `munger-advisor` skill for the framework check.
```

Frontmatter is optional but recommended for debugging. The body becomes
the user-message that the Minis agent receives.

### Reply file (`outbox/<ts>-<topic>.md`)

```markdown
---
id: 2026-09-10T18:00:05Z-card-summary
from: minis-iphone
to: hermes-desktop
in_reply_to: 2026-09-10T18:00:00Z-card-summary
completed: 2026-09-10T18:00:42Z
status: ok
---

# Re: Card summary

**Summary**

- Bullet 1 ...
- Bullet 2 ...
- Bullet 3 ...

**Munger check**

The thesis holds up on incentives and inversion but breaks on second-order
effects — the author treats network effects as a moat without checking
whether users actually face switching costs.
```

Set `status: error` and put the traceback in the body if the request
fails. The Hermes side will surface it instead of the usual reply.

## Installation

### Step 1 — Create the queue directory on iCloud Drive

On the iPhone, in the Files app:

1. Open iCloud Drive → long-press → **New Folder** → name it `Hermes-Minis`
2. Inside it create `inbox/`, `outbox/`, `processed/`
3. The full paths become:
   - `/var/minis/mounts/iCloud/Hermes-Minis/inbox/`  (Minis view)
   - iCloud Drive / `Hermes-Minis/`                  (Files app view)

On Mac or Windows the same directory appears at:

- macOS: `~/Library/Mobile Documents/com~apple~CloudDocs/Hermes-Minis/`
- Windows: `%USERPROFILE%\iCloudDrive\Hermes-Minis\` (iCloud for Windows)

> If the `iCloudDrive` folder does not exist on Windows, install iCloud
> for Windows and sign in. Without it, the Hermes side cannot reach the
> queue from Windows.

### Step 2 — Install the Minis-side skill

In a Minis conversation:

```
Install this skill and clone the full repo into /var/minis/skills/minis-bridge/:

https://github.com/OpenMinis/MinisSkills/tree/main/minis-bridge

After cloning, make the dispatcher and hook scripts executable:

chmod +x /var/minis/skills/minis-bridge/scripts/bridge-dispatch.sh
chmod +x /var/minis/skills/minis-bridge/scripts/agent-hook.sh
```

Confirm `scripts/bridge-dispatch.sh` can read the inbox dir (it
defaults to `/var/minis/mounts/iCloud/Hermes-Minis/` — override with
`MINIS_BRIDGE_QUEUE` if your mount is elsewhere).

If the repo URL does not yet contain this skill (it is being contributed
upstream), use the local-install fallback described at the end of this
document.

### Step 3 — Install the Hermes-side helper

On the machine running Hermes Desktop:

```bash
# from your Hermes skills repo
cp scripts/minis_bridge.py ~/hermes-home/skills/minis-bridge/scripts/
chmod +x ~/hermes-home/skills/minis-bridge/scripts/minis_bridge.py
```

Set the queue path:

```bash
# macOS
export MINIS_BRIDGE_QUEUE="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Hermes-Minis"

# Linux / Windows (with iCloud for Windows)
export MINIS_BRIDGE_QUEUE="$HOME/iCloudDrive/Hermes-Minis"
```

### Step 4 — Start the Minis-side loop

In a Minis conversation:

```
Start the minis-bridge loop. It should:
  - Poll /var/minis/mounts/iCloud/Hermes-Minis/inbox/ every 15 seconds
  - For each *.md file found, run it through you (the agent) as a user message
  - Write the agent's reply to /var/minis/mounts/iCloud/Hermes-Minis/outbox/
  - Move the request file to /var/minis/mounts/iCloud/Hermes-Minis/processed/
  - Log each dispatch to /var/minis/memory/minis-bridge.log

Confirm by listing the inbox dir and reporting the count of pending files.
```

The loop is a long-running shell process inside Minis's iSH sandbox.
Minis can keep it alive across conversations via a backgrounded shell
session, or the loop can run only while the conversation is open and
re-trigger when you start a new one.

### Step 5 — Test from Hermes side

From a terminal:

```bash
python ~/hermes-home/skills/minis-bridge/scripts/minis_bridge.py \
  --send "Hello from Hermes. Reply with a one-line ack."
```

Within 15–45 seconds you should see a reply printed to stdout. If you do
not, check `/var/minis/memory/minis-bridge.log` on the iPhone and the
queue's `processed/` directory to see whether the request was picked up.

## Usage patterns

### One-shot ask

```bash
python minis_bridge.py --send "Summarise today's calendar in 3 bullets."
```

### One-shot with a pre-fetched file

If you want to send Minis a file (a long prompt, a transcript, an
article body), write it locally and pass the path:

```bash
python minis_bridge.py \
  --send "Run munger-advisor on the attached Substack article." \
  --send-file ./substack-article.md
```

The helper reads the file and inlines its body into the request.
Frontmatter is auto-extracted if present, otherwise the entire file
becomes the user message. If you only have a URL, fetch the page
yourself first (`curl`, `wget`, `browser_use` on Hermes side) and
then pass the saved file with `--send-file`.

### Synchronous wait (block until reply)

```bash
python minis_bridge.py \
  --send "..." \
  --wait 60        # seconds, default 120
```

### Async (fire-and-forget)

```bash
python minis_bridge.py --send "..." --no-wait
# poll later:
python minis_bridge.py --read latest
```

### Conversation continuation

The bridge treats each request as a fresh Minis session. To continue a
thread, include the previous `id` in the new request:

```markdown
---
in_reply_to: 2026-09-10T18:00:05Z-card-summary
---

Continue the Munger check — apply the same framework to the second
article at https://example.com/article-2.
```

Minis will pass that context into its reply, but the agent itself does
not preserve conversation state across requests. If you want true
continuation, run the exchange inside a single Minis conversation and
use the bridge only to push new prompts into it (requires a second
helper that talks to Minis's debug server — see "Advanced" below).

## Failure modes and recovery

### "inbox/ stays empty on Minis side"

iCloud Drive sync is delayed or paused. Check:

1. iPhone Settings → Apple ID → iCloud → iCloud Drive → toggle off and on
2. Files app → iCloud Drive → Hermes-Minis → verify Hermes's file appears
3. Minis → Files → `/var/minis/mounts/iCloud/Hermes-Minis/inbox/` →
   verify the file is there from Minis's view

If sync is healthy but Minis still does not see the file, the iCloud
mount in Minis may need to be re-added. Minis → Settings → Mounts →
remove and re-add iCloud Drive.

### "Reply never lands in outbox/"

The Minis-side loop crashed. Check `/var/minis/memory/minis-bridge.log`
on the iPhone. Common causes:

- iSH ran out of memory — close other apps and restart the loop
- The agent errored on the request body — the reply file will exist with
  `status: error` and a traceback
- iCloud quota exceeded — Apple will silently stop syncing new writes

### "Hermes-side helper cannot find the queue"

`MINIS_BRIDGE_QUEUE` is unset or points to a non-mounted path. On
Windows, install iCloud for Windows and sign in; the folder appears
under `%USERPROFILE%\iCloudDrive\`. On Linux, iCloud Drive is not
natively supported — use a Mac or Windows box, or run Hermes on the
same Mac that owns the iCloud account.

### "iCloud quota is full"

Apple will not raise an error — it just stops syncing. Free space on the
account that owns the Hermes-Minis folder. The bridge does not prune
old messages; consider archiving `processed/` weekly.

## Security

- The queue directory is just a normal iCloud Drive folder. Anyone with
  access to that iCloud account can read every request and reply.
- Do not put API keys, passwords, or private health data into request
  bodies. Use env-var references instead (e.g. `Use the key in
  MEMOS_TOKEN env`).
- The bridge runs as the Minis iSH user. iOS sandboxing prevents it
  from touching anything outside Minis's container, so a runaway loop
  cannot damage the rest of the phone.
- Disable the loop (just stop the conversation) to immediately halt all
  pending dispatches. There is no remote kill switch.

## Local-install fallback (if the upstream PR is not merged yet)

If `OpenMinis/MinisSkills/minis-bridge/` does not yet exist on GitHub,
install from a local copy:

1. From your Hermes skills repo, copy the whole `minis-bridge/` folder
2. On the iPhone, in Minis → Settings → Mounts → make sure iCloud Drive
   is mounted
3. Drop the `minis-bridge/` folder into `/var/minis/skills/` via the
   Minis file manager (Files → navigate to `/var/minis/skills/` →
   upload)
4. Restart any open Minis conversation so the skill list refreshes
5. Continue from Step 4 of the normal install

## Advanced: talking to Minis's debug server

For true conversation continuation and lower latency, Minis exposes a
debug HTTP server when launched with the right env vars (see
`NSLocalNetworkUsageDescription` in Minis's Info.plist — the app is
already wired for this). With the debug server reachable over Tailscale:

```
http://<iphone-tailnet-ip>:debug-port/v1/chat
```

the bridge can push prompts into an existing conversation instead of
starting a new one per request. This path is **not** in this skill's
default scope — if you want it, file an issue or extend the
`scripts/bridge-dispatch.sh` helper.

## Related

- `shortcut-share-file` — the proven way to push files into Minis via
  iOS Shortcuts; useful for one-off file drops that do not need a reply
- `feishu-hermes-bridge` — bidirectional bridge pattern for Feishu,
  same shape (inbox-style queue) but on a different transport
- `email-ai-gateway` — IMAP/SMTP-based bridge for email; the third
  transport in this family of inbound → outbound agent loops
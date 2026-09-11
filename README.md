# minis-bridge

Bidirectional message bridge between [Open Minis](https://github.com/OpenMinis/OpenMinis) (iPhone / iPad) and a desktop agent runtime (e.g. [Hermes Agent](https://github.com/just-every/hermes-agent)), via an **iCloud Drive** message-queue directory.

No HTTP server. No MCP. No webhook. No SSH. The transport is iCloud Drive sync, which Apple already encrypts end-to-end and which Minis already exposes as a mounted filesystem inside its on-device iSH sandbox.

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

## What you get

- A `SKILL.md` describing the protocol and the Minis-side install.
- `scripts/bridge-dispatch.sh` — a single-tick Minis-side dispatcher.
- `scripts/agent-hook.sh` — a pluggable hook the dispatcher calls to
  forward a request into the live Minis conversation. The default
  hook is a placeholder; see the SKILL doc for two working
  implementations.
- `scripts/minis_bridge.py` — a Hermes-side CLI: `--send`,
  `--send-file`, `--read`, `--wait`, `--no-wait`.

## Install

### 1. Create the queue on iCloud Drive

In the iPhone Files app:

```
iCloud Drive / Hermes-Minis /
  ├── inbox/
  ├── outbox/
  └── processed/
```

Minis will see this as `/var/minis/mounts/iCloud/Hermes-Minis/`.

### 2. Install the Minis-side skill

In a Minis conversation, ask the agent to:

```
Install this skill. Copy SKILL.md to /var/minis/skills/minis-bridge/
and the three scripts under scripts/ to
/var/minis/skills/minis-bridge/scripts/, with execute permission.
```

Or, if you have a local clone of this repo, drop the `minis-bridge/`
folder into Minis's `/var/minis/skills/` via the Files app.

### 3. Install the Hermes-side helper

```bash
# from this repo
cp scripts/minis_bridge.py ~/hermes-home/skills/minis-bridge/scripts/
chmod +x ~/hermes-home/skills/minis-bridge/scripts/minis_bridge.py
```

Set the queue path:

```bash
# macOS
export MINIS_BRIDGE_QUEUE="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Hermes-Minis"

# Windows (with iCloud for Windows)
export MINIS_BRIDGE_QUEUE="$USERPROFILE/iCloudDrive/Hermes-Minis"
```

### 4. Start the Minis-side loop

In a Minis conversation:

```
Start the minis-bridge loop. Every 20 seconds run
/var/minis/skills/minis-bridge/scripts/bridge-dispatch.sh and log
each tick to /var/minis/memory/minis-bridge.log. Confirm by listing
inbox/ and reporting the count of pending files.
```

### 5. Test

```bash
python minis_bridge.py --send "Hello from Hermes. Reply with one-line ack." --wait 60
```

You should see the reply printed within 15–90 seconds.

## File protocol

Every request and reply is a single Markdown file. See `SKILL.md` for
the full frontmatter and body schema.

## Related

- [Open Minis](https://github.com/OpenMinis/OpenMinis) — the iOS agent app.
- [OpenMinis/MinisSkills](https://github.com/OpenMinis/MinisSkills) — the upstream community skill catalog.

## License

Apache 2.0. See `LICENSE`.
# Release notes

## v1.3.0 — 2026-09-12 — Minis-initiated push messages

- Added `[MINIS_PUSH]` and `[MINIS_PUSH_ACK]` envelopes.
- Minis can start a message with `--push TEXT [--id ID]` without an existing
  Windows request.
- Hermes receives the oldest unacknowledged message with `--pull-push` and
  confirms it with `--ack-push ID`.
- Acknowledged pushes are excluded from later pulls.
- Test suite expanded from 7 to 10 tests; all pass.

This adds durable reverse delivery over Feishu. Automatic injection into an
already-open Hermes Desktop conversation is intentionally not claimed; that
requires a separately configured receiver/cron or gateway hook.

---

## v1.2.1 — 2026-09-12 — real iPhone E2E + reply idempotency

### Complete real-device result

```text
request id: iphone-minis-smoke-1789192695
Win request:  2026-09-12T05:58:19.780Z
first reply:  2026-09-12T14:30:12.373Z
latency:      30,712.593 s (includes waiting for manual setup)
result:       MINIS_FEISHU_E2E_OK
```

The iPhone Minis session pulled and processed the original Windows request,
replied with the same ID, and Windows read back the non-empty answer. This is
the first complete Win Hermes ↔ iPhone Minis E2E acceptance pass.

### Duplicate-reply fix

The first run created two replies for the same ID, 15.342 seconds apart.
`--reply` now queries history first; if the ID already has a reply, it prints
`<id> already replied` and sends nothing.

Verification:

```text
python -m unittest discover -s tests -v
Ran 7 tests — OK

real Feishu idempotency probe:
iphone-minis-smoke-1789192695 already replied
```

### iPhone deployment confirmation

A second real request upgraded the iPhone installation and verified the fix:

```text
request id: iphone-minis-v121-1789226073
request: 2026-09-12T15:14:44.392Z
reply:   2026-09-12T15:28:00.358Z
elapsed: 795.966 s (includes manual-trigger delay)
result:  MINIS_V121_UPDATED
Feishu history: 1 request, 1 reply
```

The Minis device reported commit `3591db1`, SKILL v1.2.1, idempotency code
present, and `--help` passing. The independent one-reply count confirms the
fix is deployed rather than merely documented.

---

## v1.2.0 — 2026-09-12 — Feishu relay becomes the default

### Why

Real-device testing invalidated the core iCloud assumption. Files App could
see files written by the Mac, while Minis/iSH kept a frozen FUSE view:
`readdir` returned an empty directory, direct `stat/open/os.access` returned
not found, and directory inode mtimes did not refresh. A Mac-created sentinel
also remained invisible. The iCloud path is therefore legacy, not a working
default.

### What changed

- Added `scripts/feishu_relay.py`, a standard-library-only Feishu transport.
- Added `[MINIS_REQ]` / `[MINIS_REPLY]` envelopes with request IDs.
- Added `--send`, `--pull`, `--reply`, and `--read` commands.
- Added `references/feishu-relay.md` with Minis install and processing steps.
- Updated SKILL.md and README: Feishu is default; iCloud is clearly legacy.
- Added six unit tests covering round-trip parsing, pending filtering, send,
  pull, reply, and precise reply lookup.

### Verification

```text
python -m unittest discover -s tests -v
Ran 6 tests — OK

real Feishu transport loop:
send → pull → reply → read
request id: minis-e2e-1789191679
result: Feishu relay transport loop OK.
```

This proves the Feishu transport layer. It does **not** yet prove the final
Minis-agent hop; that requires the iPhone to execute `--pull`, process the
body, and execute `--reply` once.

### Security

No credential values are committed. `FEISHU_APP_SECRET` stays in local
Environment Variables or `.feishu.env` / `.minis-feishu.env`.

---

## v1.1.0 — 2026-09-12 — Win → Mac mini SSH wrapper

This release adds a reliable transport for Windows users. The
Microsoft Store build of iCloud Drive is known to throttle or stall
uploads; in testing today, files written to
`%USERPROFILE%\iCloudDrive\Hermes-Minis\inbox\` on a Win 11 box
never reached the iPhone Minis client, even though the local
`iCloudDrive.exe` process looked alive and CPU-active.

The Mac mini, by contrast, has been syncing reliably for weeks
(see the v5 health-shortcut upload pipeline).

### What changed

- **New script: `scripts/minis_bridge_mac.py`** — a small SSH
  wrapper on the Win Hermes box that forwards every `--send` /
  `--read` to `/Users/kakyo/scripts/minis_bridge.py` on the Mac
  mini over the existing `kanas-lan` SSH alias.
- **SKILL.md** — new "Win via Mac mini (recommended for Windows
  users)" section with architecture diagram, install steps for
  both sides, env-var overrides, and an SSH-config gotcha.
- **README / SKILL.md frontmatter** — version bumped to 1.1.0.

### What's untouched

- `minis_bridge.py` (the local script) — unchanged. Mac users
  still call it directly.
- `bridge-dispatch.sh` and `agent-hook.sh` — unchanged. The
  Minis-side loop is the same.
- `LICENSE`, `.gitignore` — unchanged.

### Verified end-to-end (2026-09-12)

```
$ python minis_bridge_mac.py --send "Bridge test via Mac mini (SSH wrapper)." --no-wait
sent 2026-09-12T03-39-23Z-bridge-test-via-mac-mini-ssh-wrapper-con -> /Users/kakyo/Library/Mobile Documents/com~apple~CloudDocs/Hermes-Minis/inbox/2026-09-12T03-39-23Z-bridge-test-via-mac-mini-ssh-wrapper-con.md
2026-09-12T03-39-23Z-bridge-test-via-mac-mini-ssh-wrapper-con

$ ssh kanas-lan "ls -la '/Users/kakyo/Library/Mobile Documents/com~apple~CloudDocs/Hermes-Minis/inbox/'"
total 16
-rw-r--r--  1 kakyo  staff  216 Sep 12 11:38 2026-09-12T03-38-26Z-smoke-test-from-windows-via-mac-summaris.md
-rw-r--r--  1 kakyo  staff  244 Sep 12 11:39 2026-09-12T03-39-23Z-bridge-test-via-mac-mini-ssh-wrapper-con.md
```

### SSH gotcha that was hit during install

`~/.ssh/config` had `kanas-lan` pointing at
`IdentityFile ~/.ssh/id_ed25519_kanas`, but the Mac mini only
accepts `id_ed25519`. Symptom:

```
$ ssh kanas-lan "echo ok"
kakyo@192.168.71.78: Permission denied (publickey,password,keyboard-interactive).
```

Fix:

```sshconfig
Host kanas-lan
    HostName 192.168.71.78
    User kakyo
    IdentityFile ~/.ssh/id_ed25519
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
```

### Commits

- `351fbaba` — feat: add minis_bridge_mac.py
- `aac9ad81` — docs: SKILL.md v1.1.0 — add 'Win via Mac mini' section

### Migration

Nothing to do for v1.0.x users:

- Mac / Linux users: keep calling `minis_bridge.py` as before.
- Win users on the Apple-installer build of iCloud Drive: keep
  calling `minis_bridge.py` as before.
- Win users on the Store build: switch to `minis_bridge_mac.py`
  after putting `minis_bridge.py` on the Mac mini.

---

## v1.0.1 — 2026-09-11 — docs: fix SKILL.md drift

This is a documentation-only release. No code changes, no API changes,
no new functionality. The Python script and shell scripts are byte-for-byte
identical to v1.0.0.

### What was wrong

The original v1.0.0 `SKILL.md` referenced things that did not exist in
the repo:

1. **Step 2 — "verify `bridge-loop.sh` is executable"** — there is no
   `scripts/bridge-loop.sh` in this repo. The dispatcher that actually
   exists is `scripts/bridge-dispatch.sh`, plus `scripts/agent-hook.sh`
   which the dispatcher calls.

2. **"One-shot with attachment" example — `--attach <URL>`** — the
   argparse in `scripts/minis_bridge.py` never declared `--attach`. The
   example was copy-paste-fictional; running it would have produced an
   argparse error.

3. **A brief `--link <URL>` variant** — also never implemented. Caught
   before this commit went out.

### What changed

`SKILL.md` now matches the actual code:

- Step 2 instructs `chmod +x` on the two scripts that exist.
- The "one-shot with a file" example uses the real `--send-file` flag
  with the file path the helper actually accepts.
- Every `--flag` mentioned in the README is defined in
  `minis_bridge.py`'s argparse.

### Verified

```
# what SKILL.md mentions         # what argparse defines
--send                            --send  ✓
--send-file                       --send-file  ✓
--read                            --read  ✓
--wait                            --wait  ✓
--no-wait                         --no-wait  ✓
```

No `--attach`. No `--link`. No `--bridge-loop`.

### Migration

Nothing to do. If you installed v1.0.0 and ran the broken examples, the
fix is just a doc update — your scripts run exactly as before.

### Commit

`92406f81` on `main`.

---

## v1.0.0 — 2026-09-11 — initial release

First public cut of the minis-bridge skill.

- Bidirectional iCloud Drive message-queue bridge between Hermes
  Desktop and Open Minis on iPhone.
- `SKILL.md` describing the protocol and the install.
- `scripts/bridge-dispatch.sh` — Minis-side single-tick dispatcher.
- `scripts/agent-hook.sh` — pluggable agent bridge (placeholder default).
- `scripts/minis_bridge.py` — Hermes-side Python CLI.
- Apache 2.0 license.

Commit `f7e9dfb` on `main`.
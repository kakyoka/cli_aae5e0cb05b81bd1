# Release notes

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
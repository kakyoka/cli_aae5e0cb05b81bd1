#!/usr/bin/env python3
# minis-bridge/scripts/minis_bridge.py
# Hermes-side helper for the Minis bridge. Writes request files to the
# iCloud Drive queue and waits for replies.
#
# Usage:
#   python minis_bridge.py --send "Hello" [--wait 60] [--no-wait]
#   python minis_bridge.py --send-file path/to/request.md
#   python minis_bridge.py --read latest
#   python minis_bridge.py --read <id>
#
# Env:
#   MINIS_BRIDGE_QUEUE   absolute path to the Hermes-Minis queue dir.
#                        macOS default:  ~/Library/Mobile Documents/com~apple~CloudDocs/Hermes-Minis
#                        Win default:    %USERPROFILE%\\iCloudDrive\\Hermes-Minis
#                        Linux default:  no default — set this.

import argparse
import datetime as dt
import os
import pathlib
import re
import sys
import time

DEFAULT_PATHS = {
    "darwin":  "~/Library/Mobile Documents/com~apple~CloudDocs/Hermes-Minis",
    "win32":   "~/iCloudDrive/Hermes-Minis",
}


def queue_dir() -> pathlib.Path:
    raw = os.environ.get("MINIS_BRIDGE_QUEUE")
    if raw:
        return pathlib.Path(raw).expanduser()
    plat = sys.platform
    default = DEFAULT_PATHS.get(plat)
    if not default:
        sys.exit(
            "MINIS_BRIDGE_QUEUE is not set and there is no default for "
            f"platform {plat!r}. Set the env var to the absolute path of "
            "the Hermes-Minis iCloud Drive folder."
        )
    p = pathlib.Path(default).expanduser()
    if not p.exists():
        sys.exit(
            f"Default queue dir {p} does not exist. iCloud Drive may not "
            "be mounted on this machine. Install iCloud for Windows or "
            "sign in to iCloud on macOS, or set MINIS_BRIDGE_QUEUE to "
            "the right path."
        )
    return p


def ts() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def slugify(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text.strip().lower())[:40].strip("-")
    return s or "msg"


def send(text: str, wait: int, no_wait: bool) -> int:
    q = queue_dir()
    inbox = q / "inbox"
    outbox = q / "outbox"
    inbox.mkdir(parents=True, exist_ok=True)
    outbox.mkdir(parents=True, exist_ok=True)

    req_id = f"{ts()}-{slugify(text)}"
    path = inbox / f"{req_id}.md"
    body = (
        f"---\n"
        f"id: {req_id}\n"
        f"from: hermes-desktop\n"
        f"to: minis-iphone\n"
        f"created: {dt.datetime.now(dt.timezone.utc).isoformat()}\n"
        f"---\n\n"
        f"{text}\n"
    )
    path.write_text(body, encoding="utf-8")
    print(f"sent {req_id} -> {path}", file=sys.stderr)

    if no_wait:
        print(req_id)
        return 0

    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        reply = outbox / f"{req_id}.md"
        if reply.exists():
            print(reply.read_text(encoding="utf-8"))
            return 0
        time.sleep(2)

    print(
        f"timed out after {wait}s waiting for {req_id}. The Minis bridge "
        "loop may not be running. Check /var/minis/memory/minis-bridge.log "
        "on the iPhone and the inbox/processed directories.",
        file=sys.stderr,
    )
    return 2


def send_file(path: pathlib.Path, wait: int, no_wait: bool) -> int:
    if not path.exists():
        sys.exit(f"file not found: {path}")
    body = path.read_text(encoding="utf-8")
    m = re.search(r"^---\s*$", body, re.M)
    m2 = re.search(r"^---\s*$", body[m.end():], re.M) if m else None
    if m2:
        text = body[m.end() + m2.end():].lstrip()
    else:
        text = body
    return send(text, wait=wait, no_wait=no_wait)


def read(target: str) -> int:
    q = queue_dir()
    outbox = q / "outbox"
    if not outbox.exists():
        sys.exit(f"outbox does not exist: {outbox}")
    files = sorted(outbox.glob("*.md"), key=lambda p: p.stat().st_mtime)
    if not files:
        print("(no replies yet)", file=sys.stderr)
        return 1
    if target == "latest":
        chosen = files[-1]
    else:
        chosen = next((f for f in files if target in f.stem), None)
        if not chosen:
            print(f"no reply matching {target!r}", file=sys.stderr)
            return 1
    print(chosen.read_text(encoding="utf-8"))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Hermes ↔ Minis bridge helper")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--send", metavar="TEXT", help="send a one-shot prompt")
    g.add_argument("--send-file", metavar="PATH", type=pathlib.Path,
                   help="send the body of an existing .md request file")
    g.add_argument("--read", metavar="ID_OR_LATEST",
                   help="print a reply from outbox/ (use 'latest' for newest)")
    p.add_argument("--wait", type=int, default=120,
                   help="seconds to wait for a reply (default 120)")
    p.add_argument("--no-wait", action="store_true",
                   help="fire-and-forget: return after writing the request")
    args = p.parse_args()

    if args.send is not None:
        return send(args.send, wait=args.wait, no_wait=args.no_wait)
    if args.send_file is not None:
        return send_file(args.send_file, wait=args.wait, no_wait=args.no_wait)
    if args.read is not None:
        return read(args.read)
    return 0


if __name__ == "__main__":
    sys.exit(main())
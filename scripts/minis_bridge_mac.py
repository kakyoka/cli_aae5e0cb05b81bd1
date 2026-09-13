#!/usr/bin/env python3
"""
minis_bridge via Mac mini (SSH wrapper)

Why this exists: iCloud Drive on Windows 11 (Microsoft Store build)
is known to throttle or stall uploads — your Mac mini, by contrast,
syncs reliably. This wrapper turns every Hermes-side minis-bridge
call into an SSH invocation against kanas-lan, which runs
/Users/kakyo/scripts/minis_bridge.py against the real iCloud Drive
path on the Mac.

Same CLI surface as the local minis_bridge.py:
  python minis_bridge_mac.py --send "..." [--wait 60] [--no-wait]
  python minis_bridge_mac.py --read latest
  python minis_bridge_mac.py --read <id>

The script forwards --send / --send-file / --read to the remote
minis_bridge.py via SSH and streams the remote stdout / stderr back.

Env (optional):
  MINIS_BRIDGE_MAC_HOST   default: kanas-lan (your SSH alias)
  MINIS_BRIDGE_MAC_SCRIPT default: /Users/kakyo/scripts/minis_bridge.py
  MINIS_BRIDGE_MAC_QUEUE  default: ~/Library/Mobile Documents/com~apple~CloudDocs/Hermes-Minis
"""
import argparse
import pathlib
import subprocess
import sys

DEFAULT_HOST = "kanas-lan"
DEFAULT_SCRIPT = "/Users/kakyo/scripts/minis_bridge.py"
DEFAULT_QUEUE = "~/Library/Mobile Documents/com~apple~CloudDocs/Hermes-Minis"


def remote() -> str:
    import os
    return os.environ.get("MINIS_BRIDGE_MAC_HOST", DEFAULT_HOST)


def remote_script() -> str:
    import os
    return os.environ.get("MINIS_BRIDGE_MAC_SCRIPT", DEFAULT_SCRIPT)


def remote_queue() -> str:
    import os
    return os.environ.get("MINIS_BRIDGE_MAC_QUEUE", DEFAULT_QUEUE)


def shell_quote(s: str) -> str:
    """Quote a string for use in a remote sh -c '...' command."""
    return "'" + s.replace("'", "'\\''") + "'"


def run_remote(args: list[str]) -> int:
    """Run the remote minis_bridge.py with the given arg list and stream output."""
    quoted_args = " ".join(shell_quote(a) for a in args)
    cmd = (
        f"export MINIS_BRIDGE_QUEUE={shell_quote(remote_queue())}; "
        f"python3 {remote_script()} {quoted_args}"
    )
    proc = subprocess.run(
        ["ssh", remote(), cmd],
        text=True,
    )
    return proc.returncode


def main() -> int:
    p = argparse.ArgumentParser(
        description="Hermes -> Mac mini -> iCloud Drive -> Minis bridge",
        epilog="This wrapper forwards to /Users/kakyo/scripts/minis_bridge.py via SSH.",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--send", metavar="TEXT")
    g.add_argument("--read", metavar="ID_OR_LATEST")
    p.add_argument("--wait", type=int, default=120)
    p.add_argument("--no-wait", action="store_true")

    args = p.parse_args()

    remote_args = []
    if args.send is not None:
        remote_args += ["--send", args.send]
        if args.no_wait:
            remote_args += ["--no-wait"]
        else:
            remote_args += ["--wait", str(args.wait)]
    elif args.read is not None:
        remote_args += ["--read", args.read]

    return run_remote(remote_args)


if __name__ == "__main__":
    sys.exit(main())

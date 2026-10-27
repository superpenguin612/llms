"""kb command-line entry point. Each command module defines register(subparsers, common)."""

import argparse
import importlib
import sys

from kb import __version__
from kb.util import KBError

# Command modules, in help order. A module that doesn't exist yet is skipped.
MODULES = ["init", "lint", "build", "query", "capture", "refresh", "collect", "triage", "queue",
           "watch", "snapshot", "registry", "sessions", "migrate"]


def build_parser() -> argparse.ArgumentParser:
    def flags(suppress: bool) -> argparse.ArgumentParser:
        # Subcommands get SUPPRESS defaults so `kb --vault X cmd` isn't reset by the subparser's copy.
        d = {"default": argparse.SUPPRESS} if suppress else {}
        c = argparse.ArgumentParser(add_help=False)
        c.add_argument("--vault", metavar="NAME", help="use a vault from the registry instead of the current directory", **d)
        c.add_argument("--json", action="store_true", help="machine-readable output", **d)
        return c

    common = flags(suppress=True)
    p = argparse.ArgumentParser(prog="kb", parents=[flags(suppress=False)],
                                description="Knowledge-base CLI: capture, triage, lint, index and query kb vaults.")
    p.add_argument("--version", action="version", version=f"kb {__version__}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")
    for name in MODULES:
        try:
            mod = importlib.import_module(f"kb.{name}")
        except ModuleNotFoundError as e:
            if e.name == f"kb.{name}":
                continue
            raise
        mod.register(sub, common)
    return p


def main(argv=None) -> int:
    p = build_parser()
    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help()
        return 0
    try:
        return int(args.func(args) or 0)
    except KBError as e:
        print(f"kb {args.command}: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())

"""CLI output never contains remote error bodies or Python tracebacks."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import sqlite3
import sys

from .archive import Archive, atomic_write
from .pipeline import sync
from .security import PipelineError


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes raw user arguments, potentially secret URLs.
        raise PipelineError("invalid_arguments")


def parser():
    root = SafeParser(prog="personal-library")
    commands = root.add_subparsers(dest="command", required=True)
    sync_parser = commands.add_parser("sync", help="Archive a bounded batch of public bookmarks")
    sync_parser.add_argument("--source", type=Path, default=Path("README.md"))
    sync_parser.add_argument("--output", type=Path, required=True)
    sync_parser.add_argument("--limit", type=int, default=10)
    sync_parser.add_argument("--url")
    sync_parser.add_argument("--retry-failed", action="store_true")
    sync_parser.add_argument("--max-attempts", type=int, default=3)
    sync_parser.add_argument("--wayback", action="store_true")
    weekly = commands.add_parser("weekly", help="Deterministic UTC ISO-week digest")
    weekly.add_argument("--output", type=Path, required=True)
    weekly.add_argument("--week", default=datetime.now(timezone.utc).strftime("%G-W%V"))
    weekly.add_argument("--write", action="store_true")
    search = commands.add_parser("search", help="Search archived successful documents")
    search.add_argument("--output", type=Path, required=True)
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=20)
    return root


def handle_sigterm(signum, frame):
    # SystemExit bypasses per-entry Exception handlers while unwinding locks.
    raise SystemExit(124)


def main(argv=None) -> int:
    previous_sigterm = signal.signal(signal.SIGTERM, handle_sigterm)
    try:
        args = parser().parse_args(argv)
        if args.command == "sync":
            counts = sync(
                args.source, args.output, limit=args.limit, url=args.url,
                retry_failed=args.retry_failed, max_attempts=args.max_attempts,
                wayback=args.wayback,
            )
            print(json.dumps(counts, sort_keys=True))
            return 1 if counts["attempted"] and counts["failed"] == counts["attempted"] else 0
        if not (args.output / "data.json").is_file():
            raise PipelineError("archive_not_initialized")
        with Archive(args.output) as archive:
            if args.command == "search":
                print(json.dumps(archive.search(args.query, args.limit), ensure_ascii=False))
            else:
                markdown = archive.weekly(args.week)
                if args.write:
                    atomic_write(archive.path(f"weekly/{args.week}.md"), markdown)
                    print(json.dumps({"written": 1}))
                else:
                    print(markdown, end="")
        return 0
    except PipelineError as error:
        print(json.dumps({"error": error.code}), file=sys.stderr)
        return 2
    except (OSError, UnicodeError, sqlite3.Error, ValueError):
        print(json.dumps({"error": "local_io_or_state_failed"}), file=sys.stderr)
        return 2
    except Exception:
        print(json.dumps({"error": "internal_error"}), file=sys.stderr)
        return 2
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == "__main__":
    raise SystemExit(main())

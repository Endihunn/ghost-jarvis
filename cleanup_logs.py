"""Ghost Jarvis — Log cleanup utility.

Deletes log files older than a given number of days.
Can be run manually or scheduled via Windows Task Scheduler.

Examples:
    python cleanup_logs.py              # default 3 days
    python cleanup_logs.py --days 7     # keep last 7 days
    python cleanup_logs.py --dry-run    # preview only, don't delete
"""
import argparse
import sys
import time
from pathlib import Path


DEFAULT_DAYS = 3
LOG_PATTERNS = ("*.log", "*.log.*")


def clean_logs(
    directory: Path,
    days: int = DEFAULT_DAYS,
    dry_run: bool = False,
) -> list[Path]:
    """Return list of files that would be/were deleted."""
    if not directory.exists():
        return []

    cutoff = time.time() - (days * 86400)
    to_remove: list[Path] = []

    for pattern in LOG_PATTERNS:
        for fpath in directory.glob(pattern):
            if not fpath.is_file():
                continue
            try:
                if fpath.stat().st_mtime < cutoff:
                    to_remove.append(fpath)
            except OSError:
                pass

    if not dry_run:
        for fpath in to_remove:
            try:
                fpath.unlink()
            except OSError as e:
                print(f"  [WARN] Could not delete {fpath}: {e}", file=sys.stderr)

    return to_remove


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete Ghost Jarvis log files older than N days."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"Retention period in days (default: {DEFAULT_DAYS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List files without deleting them",
    )
    args = parser.parse_args()

    app_dir = Path(__file__).parent.resolve()
    log_dir = app_dir / "logs"

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Cleaning logs older than {args.days} day(s)...")
    print(f"  Directory: {log_dir}")

    removed = clean_logs(log_dir, days=args.days, dry_run=args.dry_run)

    # Also check app.log in the app root
    app_log = app_dir / "app.log"
    cutoff = time.time() - (args.days * 86400)
    try:
        if app_log.exists() and app_log.stat().st_mtime < cutoff:
            removed.append(app_log)
            if not args.dry_run:
                app_log.unlink()
    except OSError as e:
        print(f"  [WARN] Could not delete {app_log}: {e}", file=sys.stderr)

    if not removed:
        print("  [OK] No old log files found.")
        return 0

    print(f"  [{'DRY RUN' if args.dry_run else 'OK'}] {len(removed)} file(s) {'would be deleted' if args.dry_run else 'deleted'}:")
    for f in removed:
        age_days = int((time.time() - f.stat().st_mtime) / 86400)
        print(f"     - {f.name}  ({age_days} days old)")

    return 0


if __name__ == "__main__":
    sys.exit(main())

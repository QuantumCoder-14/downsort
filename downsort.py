#!/usr/bin/env python3
"""
downsort - sort a messy Downloads folder into typed subfolders, with an undo.

Nothing moves unless you pass --apply. Every move is written to a log so you can
put everything back exactly where it was.

Usage:
    python downsort.py                     show what would happen (dry run)
    python downsort.py --apply             actually move the files
    python downsort.py --path ~/Desktop/x  sort a different folder
    python downsort.py --by-date           group inside each category by YYYY-MM
    python downsort.py --dedupe --apply    move duplicate files into _duplicates/
    python downsort.py --restore           undo the most recent --apply run
    python downsort.py --list-logs         show the logs available to restore

Stdlib only. No install step, no dependencies.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

APP_NAME = "downsort"
APP_VERSION = "1.0"
LOG_DIR = Path.home() / ".downsort"

# Files that are still being written by a browser or installer.
IN_PROGRESS_SUFFIXES = {".crdownload", ".part", ".partial", ".download", ".tmp", ".opdownload"}

CATEGORIES = {
    "images":      {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".heic", ".avif", ".tif", ".tiff"},
    "documents":   {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".ppt", ".pptx", ".odp", ".pages"},
    "spreadsheets":{".xls", ".xlsx", ".csv", ".ods", ".tsv", ".numbers"},
    "archives":    {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz", ".iso"},
    "installers":  {".exe", ".msi", ".dmg", ".pkg", ".deb", ".rpm", ".apk", ".appx"},
    "audio":       {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma"},
    "video":       {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".m4v", ".wmv"},
    "code":        {".py", ".js", ".ts", ".json", ".html", ".css", ".c", ".cpp", ".h", ".java",
                    ".kt", ".sh", ".bat", ".ps1", ".yml", ".yaml", ".toml", ".xml", ".sql"},
    "ebooks":      {".epub", ".mobi", ".azw3", ".djvu", ".cbz", ".cbr"},
}

# .tar.gz and friends: match the longest known compound suffix first.
COMPOUND_SUFFIXES = [".tar.gz", ".tar.bz2", ".tar.xz"]


def classify(filename):
    """Return the category folder for a filename. Never returns None."""
    lower = str(filename).lower()
    for compound in COMPOUND_SUFFIXES:
        if lower.endswith(compound):
            return "archives"
    suffix = Path(lower).suffix
    for category, extensions in CATEGORIES.items():
        if suffix in extensions:
            return category
    return "other"


def is_in_progress(path):
    return Path(path).suffix.lower() in IN_PROGRESS_SUFFIXES


def unique_destination(dest):
    """If dest exists, add (1), (2), ... before the suffix. Never overwrite."""
    dest = Path(dest)
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    index = 1
    while True:
        candidate = dest.with_name(f"{stem} ({index}){suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def build_plan(root, by_date=False, include_hidden=False):
    """
    Work out where every file in root should go, without touching anything.

    Returns (plan, skipped) where plan is a list of dicts with src, dst and category,
    and skipped is a list of (path, reason).
    """
    root = Path(root)
    plan, skipped = [], []

    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            continue
        if entry.name.startswith(".") and not include_hidden:
            skipped.append((entry, "hidden file"))
            continue
        if is_in_progress(entry):
            skipped.append((entry, "still downloading"))
            continue

        category = classify(entry.name)
        target_dir = root / category
        if by_date:
            stamp = datetime.fromtimestamp(entry.stat().st_mtime).strftime("%Y-%m")
            target_dir = target_dir / stamp

        plan.append({
            "src": str(entry),
            "dst": str(unique_destination(target_dir / entry.name)),
            "category": category,
            "size": entry.stat().st_size,
        })
    return plan, skipped


def apply_plan(plan):
    """Move every file in the plan. Returns the log dict that was written."""
    moves = []
    for item in plan:
        src, dst = Path(item["src"]), Path(item["dst"])
        dst.parent.mkdir(parents=True, exist_ok=True)
        # re-check right before moving: the file may have been renamed meanwhile
        if not src.exists():
            continue
        final = unique_destination(dst)
        shutil.move(str(src), str(final))
        moves.append({"src": str(src), "dst": str(final), "category": item["category"]})

    log = {
        "app": APP_NAME,
        "version": APP_VERSION,
        "when": datetime.now().isoformat(timespec="seconds"),
        "root": str(Path(plan[0]["src"]).parent) if plan else None,
        "moves": moves,
    }
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"log-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    log_path.write_text(json.dumps(log, indent=2), encoding="utf-8")
    log["log_path"] = str(log_path)
    return log


def latest_log():
    if not LOG_DIR.exists():
        return None
    logs = sorted(LOG_DIR.glob("log-*.json"))
    return logs[-1] if logs else None


def restore(log_path=None):
    """
    Undo a run by moving files back. Skips any file whose original path is occupied
    or whose moved copy has since disappeared. Returns (restored, skipped).
    """
    path = Path(log_path) if log_path else latest_log()
    if not path or not path.exists():
        return [], [("(none)", "no log file found")]
    log = json.loads(path.read_text(encoding="utf-8"))

    restored, skipped = [], []
    for move in reversed(log.get("moves", [])):
        src, dst = Path(move["src"]), Path(move["dst"])
        if not dst.exists():
            skipped.append((str(dst), "no longer there"))
            continue
        if src.exists():
            skipped.append((str(src), "original path is occupied"))
            continue
        src.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dst), str(src))
        restored.append((str(dst), str(src)))
    return restored, skipped


def file_digest(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def find_duplicates(root, min_bytes=1):
    """Group files by content hash. Returns {digest: [paths]} for groups with 2+ files."""
    root = Path(root)
    by_size = {}
    for entry in root.iterdir():
        if not entry.is_file() or entry.name.startswith("."):
            continue
        if is_in_progress(entry):
            continue
        size = entry.stat().st_size
        if size < min_bytes:
            continue
        by_size.setdefault(size, []).append(entry)

    groups = {}
    for size, candidates in by_size.items():
        if len(candidates) < 2:
            continue
        for path in candidates:
            groups.setdefault(file_digest(path), []).append(path)
    return {digest: paths for digest, paths in groups.items() if len(paths) > 1}


def human(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def print_plan(plan, skipped):
    if not plan:
        print("Nothing to sort - every file is already in a category folder.")
    else:
        by_cat = {}
        for item in plan:
            by_cat.setdefault(item["category"], []).append(item)
        total = 0
        for category in sorted(by_cat):
            items = by_cat[category]
            size = sum(i["size"] for i in items)
            total += size
            print(f"  {category:<14} {len(items):>3} file(s)  {human(size):>9}")
            for item in items[:4]:
                print(f"      {Path(item['src']).name}  ->  {Path(item['dst']).parent.name}/")
            if len(items) > 4:
                print(f"      ... and {len(items) - 4} more")
        print(f"\n  {len(plan)} file(s), {human(total)} in total.")

    if skipped:
        print("\n  Skipped:")
        for path, reason in skipped:
            print(f"      {Path(path).name}  ({reason})")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description="Sort a messy Downloads folder into typed subfolders. Dry run by default.",
    )
    parser.add_argument("--path", default=str(Path.home() / "Downloads"),
                        help="folder to sort (default: ~/Downloads)")
    parser.add_argument("--apply", action="store_true",
                        help="actually move the files (without this it only prints)")
    parser.add_argument("--by-date", action="store_true",
                        help="group inside each category by year-month")
    parser.add_argument("--include-hidden", action="store_true",
                        help="also sort dotfiles")
    parser.add_argument("--dedupe", action="store_true",
                        help="find files with identical content instead of sorting")
    parser.add_argument("--restore", action="store_true",
                        help="undo the most recent --apply run")
    parser.add_argument("--list-logs", action="store_true",
                        help="list the logs available to restore")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    args = parser.parse_args(argv)

    if args.list_logs:
        logs = sorted(LOG_DIR.glob("log-*.json")) if LOG_DIR.exists() else []
        if not logs:
            print("No runs recorded yet.")
        for log in logs:
            data = json.loads(log.read_text(encoding="utf-8"))
            print(f"  {log.name}  {len(data.get('moves', []))} file(s)  {data.get('when', '')}")
        return 0

    if args.restore:
        restored, skipped = restore()
        print(f"\n  {APP_NAME} {APP_VERSION} - restore\n")
        if not restored and not skipped:
            print("  Nothing to restore.")
            return 0
        for src, dst in restored:
            print(f"  moved back: {Path(dst).name}  ->  {Path(src).parent.name}/")
        for path, reason in skipped:
            print(f"  skipped: {Path(path).name}  ({reason})")
        print(f"\n  {len(restored)} file(s) restored, {len(skipped)} skipped.")
        return 0

    root = Path(os.path.expanduser(args.path))
    print(f"\n  {APP_NAME} {APP_VERSION} - {'DUPLICATES' if args.dedupe else 'SORT'}"
          f"{'' if args.apply else ' (dry run, nothing will move)'}\n")
    if not root.exists():
        print(f"  Folder not found: {root}")
        return 2

    if args.dedupe:
        groups = find_duplicates(root)
        if not groups:
            print("  No duplicate files found.")
            return 0
        wasted = 0
        for digest, paths in groups.items():
            keep = max(paths, key=lambda p: p.stat().st_mtime)
            dupes = [p for p in paths if p != keep]
            wasted += sum(p.stat().st_size for p in dupes)
            print(f"  keep {keep.name}")
            for dupe in dupes:
                print(f"    duplicate: {dupe.name} ({human(dupe.stat().st_size)})")
        print(f"\n  {len(groups)} group(s), about {human(wasted)} reclaimable.")
        if args.apply:
            moved = 0
            target = root / "_duplicates"
            for digest, paths in groups.items():
                keep = max(paths, key=lambda p: p.stat().st_mtime)
                for dupe in paths:
                    if dupe == keep:
                        continue
                    target.mkdir(exist_ok=True)
                    shutil.move(str(dupe), str(unique_destination(target / dupe.name)))
                    moved += 1
            print(f"  Moved {moved} duplicate(s) into {target.name}/")
        else:
            print("  Add --apply to move duplicates into _duplicates/")
        return 0

    plan, skipped = build_plan(root, by_date=args.by_date, include_hidden=args.include_hidden)
    print_plan(plan, skipped)

    if not args.apply:
        if plan:
            print("\n  This was a dry run. Re-run with --apply to move the files.")
        return 0

    if not plan:
        return 0
    log = apply_plan(plan)
    print(f"\n  Moved {len(log['moves'])} file(s).")
    print(f"  Undo any time with: python {Path(sys.argv[0]).name} --restore")
    print(f"  Log: {log['log_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

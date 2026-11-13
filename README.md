# downsort

**Sort a messy Downloads folder into typed subfolders — with an undo.**

One Python file, no dependencies, dry run by default. It reads your folder, tells you
exactly where everything would go, and moves nothing until you pass `--apply`. Every move
is logged, so `--restore` puts it all back.

```
  images           2 file(s)       2.4 MB
      Screenshot 2026-11-13.png  ->  images/
      photo.jpg  ->  images/
  documents        4 file(s)      10.1 MB
      invoice.pdf  ->  documents/
      ...
  Skipped:
      .hidden.pdf  (hidden file)
      movie.mp4.crdownload  (still downloading)
```

---

## Quick start

```bash
# 1. see the plan - nothing moves
python downsort.py

# 2. do it
python downsort.py --apply

# 3. changed your mind
python downsort.py --restore
```

No install, no `pip`. Python 3.8+ and that's it.

---

## What it does

| Flag | What it does |
| --- | --- |
| *(none)* | Dry run on `~/Downloads`: prints the plan, moves nothing |
| `--apply` | Actually moves the files |
| `--path DIR` | Sort a different folder |
| `--by-date` | Group inside each category by `YYYY-MM`, from the file's modified date |
| `--dedupe` | Find files with identical content instead of sorting; `--apply` moves extras to `_duplicates/` |
| `--restore` | Undo the most recent `--apply` run |
| `--list-logs` | Show every run you can restore |
| `--include-hidden` | Also sort dotfiles (off by default) |

### Categories

`images` · `documents` · `spreadsheets` · `archives` · `installers` · `audio` · `video` ·
`code` · `ebooks` · `other`

Compound suffixes are handled: `source.tar.gz` lands in `archives`, not `other`.

---

## The three things that make it safe

**It never overwrites.** If `documents/invoice.pdf` already exists, the incoming file
becomes `invoice (1).pdf`. Two runs on the same day cannot destroy the first one's result —
there is a test for exactly that.

**It skips files still being written.** `.crdownload`, `.part`, `.partial`, `.download`,
`.tmp` and `.opdownload` are left alone, so it will not grab a download mid-flight and
leave you with a truncated file.

**It can undo itself.** Each `--apply` writes `~/.downsort/log-<timestamp>.json` with every
source and destination. `--restore` replays it backwards. If the original path is now
occupied by something else, that file is skipped and reported rather than clobbered.

---

## What it deliberately does not do

- **No background watching.** It is a command you run, not a service that runs itself. That
  is a decision, not a missing feature: a watcher that moves files while you are saving
  them is how people lose work.
- **No subfolders.** It sorts the top level of the folder you point it at and leaves
  existing folders alone. Point it at `Downloads` after a year of use and it will not
  reorganise the folders you already made.
- **No deletion, ever.** Duplicates go to `_duplicates/`; nothing is removed.
- **No cloud awareness.** Files in OneDrive or Drive folders are moved like any other, and
  the sync client will follow them. Run a dry run first on those.

---

## Tests

```bash
python tests/test_downsort.py      # 26 tests
```

Covered: extension classification including uppercase and compound suffixes, the plan
builder, hidden and in-progress skipping, `--by-date` grouping, applying and logging,
restoring, refusing to overwrite, two-run collision handling, duplicate detection by
SHA-256, and the CLI paths including the missing-folder exit code.

---

## Licence

MIT License with Commons Clause — use it, change it, share it, don't sell it.
See [LICENSE.md](LICENSE.md).

Copyright (c) 2026 Siddhant Neekhra.

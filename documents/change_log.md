# Change log

## Versioning rules

- Every source file carries its own version tag: line 2 after a shebang
  (`#!/usr/bin/env bash` then `# v0.3`), otherwise line 1 (`# v0.3`,
  `<!-- v0.3 -->`).
- Any change to a file bumps its Minor by one (v0.3 -> v0.4). Files are
  versioned independently.
- Only the owner declares a Major bump (v0.x -> v1.0), which resets Minor.
- A script's program version (`VERSION` in `git-ignore.sh`, printed by
  `--version`) is separate from its file tag and is bumped when behavior
  changes.
- Every change gets an entry below, newest first, in the same pull request
  as the change.

## 2026-11-23 — Add git-ignore.sh, its tests, CI and the solo PR workflow

| File | Version | Change |
|---|---|---|
| git-ignore.sh | v0.1 | New. Adds a pattern to `.gitignore` and untracks matching files; `--sync` untracks everything the `.gitignore` files ignore; `--dry-run`, `--quiet`, `--version`; program version 0.1 |
| tests/test_git-ignore.sh | v0.1 | New. 52 end-to-end checks in throw-away repos; runs under bash 3.2 to 5 |
| .github/workflows/ci.yml | v0.1 | New. shellcheck on every script, tests on Linux and macOS (`/bin/bash` 3.2), one required summary check `CI passed` |
| .github/pull_request_template.md | — | New. What / why / how checked, plus the conventions checklist |
| documents/solo_pr_workflow.md | v0.1 | New. One-time GitHub setup (merge options, `main` ruleset) and the branch → PR → CI → squash-merge loop |
| documents/change_log.md | — | New. This file |
| README.md | v0.2 | git-ignore.sh section (install as `git ignore`, usage), Development section, entry-point table lists the two new entry points |

git-ignore.sh started as a hand-written script; this version keeps its
behavior and fixes what review found:

- Matching now uses git's own ignore engine (`git ls-files -c -i -x`)
  instead of hand-built pathspecs. The old pathspecs untracked too much for
  patterns with a slash in the middle (`docs/build` also hit
  `lib/docs/build`) and did not understand `**` or `!`.
- `--sync` reads only `.gitignore` files. It used `--exclude-standard`,
  which also applies `.git/info/exclude` and the user's global excludes
  file, so personal settings on one machine could untrack files and delete
  them from every other clone on the next pull.
- `--dry-run` no longer creates an empty `.gitignore`.
- Paths are passed to `git rm` on stdin with `--literal-pathspecs`: no
  argument-length limit on big folders, and a tracked name such as
  `odd[1].txt` is no longer read as a glob that untracks `odd1.txt` too.
- A failing `git ls-files` now stops the script instead of reporting
  "nothing matched".
- Command line follows cli-and-docs-conventions: bundled flags (`-sn`),
  options before or after the pattern, `--` before a pattern starting with
  `-`, `--version`, three-line usage errors with exit 2, results on stdout
  and progress on stderr, exit 130 on Ctrl-C. A pattern plus `--sync` now
  adds the pattern and then syncs everything (before, the pattern was
  silently ignored).

Evidence: `bash tests/test_git-ignore.sh` passes 52 of 52; shellcheck 0.11
reports nothing on any script; removing each of the glob, personal-excludes
and dry-run fixes makes the matching test fail.

## 2026-11-21 — Baseline

| File | Version | Change |
|---|---|---|
| git_sync.sh | v0.1 | Baseline: find every repo under a folder, fetch, and offer commit, pull (fast-forward, rebase or merge) and push; `--status`, `--no-fetch`, `--max-depth` |
| README.md | v0.1 | Baseline: install, usage, behavior, troubleshooting, license |
| LICENSE | — | PolyForm Noncommercial 1.0.0 with the required notice |

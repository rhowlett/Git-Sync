#!/usr/bin/env python3
# v0.2
################################################################################
#                                                                              #
# ███████  ███████    ██ ███████  ██   ██     ███████  ██   ██    ██ ███████   #
# ██   ██  ██   ██    ██ ██   ██  ██   ██     ██   ██  ██   ██    ██ ██   ██   #
# ██   ██  ██   ██    ██ ██       ██ ██       ██       ██   ██    ██ ██   ██   #
# ████████ ████████  ███ ███      ████████    ███      ████████  ███ ███████   #
# ███  ███ ███  ███  ███ ███      ███  ███    ███      ███  ███  ███ ███       #
# ███  ███ ███  ███  ███ ███      ███  ███    ███      ███  ███  ███ ███       #
# ███  ███ ███  ███  ███ ███  ███ ███  ███    ███  ███ ███  ███  ███ ███       #
# ████████ ███  ███  ███ ████████ ███  ███    ████████ ███  ███  ███ ███       #
#                                                                              #
################################################################################
#  Project   : git_sync
#  File      : git-resolve.py
#  Copyright : (c) 2026 brickChip and Richard Howlett. All rights reserved.
#  Author    : Richard Howlett <rhowlett@eenerd.com>
#  License   : PolyForm Noncommercial 1.0.0. Free for noncommercial use;
#              commercial use requires written permission from the owner.
#              Full terms in LICENSE at the project root.
#  Warranty  : None. Provided as is; the owner is not liable for any
#              damages arising from its use (No Liability, LICENSE).
#  BRICKCHIP-ENTRY-POINT: Resolve git conflicts one by one, then commit and push
#  SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
################################################################################

"""Resolve git conflict markers one block at a time, then commit and push.

git-resolve finds the files that a merge, cherry-pick, revert or rebase left
unmerged, shows each ``<<<<<<<`` / ``=======`` / ``>>>>>>>`` block with the
lines around it, and asks what to keep: 1 ours, 2 theirs, both, or something
different (edited in the git editor or typed in). A file is written and
staged once all of its blocks are decided. When nothing is left unmerged the
merge is committed with a message that lists every decision, and the branch
is pushed.

It is a stand-alone script meant to be called from a larger pull / branch /
merge / CI workflow: ``--take`` resolves without prompts, ``--finish`` stops
after staging or committing, ``--list`` and ``--check`` report conflicts with
exit status 3, and ``--json`` gives machine-readable results on stdout.

Streams (cli-and-docs-conventions section 5): conflict displays, prompts and
progress go to stderr; results (the decision summary, the --list and --check
reports) go to stdout, so a calling script can capture them. Color is
decided for stderr, the stream the conflict displays go to.

State: decisions from an unfinished run are kept in ``<git dir>/git-resolve.json``,
keyed by the commits of the operation in progress, so a later run's commit
message lists them all. The file is ignored when the key no longer matches
(the merge was aborted or a new one started) and deleted after the commit.

Requires Python 3.9 or later and git on PATH; standard library only. Option
text lives in the help pages pasted at the end of this file (HELP_FILES), not
in this code; run ``git-resolve --help`` to read them.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, NoReturn, Sequence

APP_NAME = "git-resolve"
APP_VERSION = "0.1"

EXIT_UNRESOLVED = 3          # conflicts remain (tool-specific code, 3-63)

# git writes markers of core.conflict-marker-size characters, default 7. Inner
# markers of a recursive merge base are longer, so an exact 7 leaves them as
# ordinary content.
MARKER_SIZE = 7
START_RE = re.compile(r"^<{%d}(?: (.*))?$" % MARKER_SIZE)
BASE_RE = re.compile(r"^\|{%d}(?: (.*))?$" % MARKER_SIZE)
SEP_RE = re.compile(r"^={%d}$" % MARKER_SIZE)
END_RE = re.compile(r"^>{%d}(?: (.*))?$" % MARKER_SIZE)

BINARY_SNIFF = 8000          # bytes inspected for NUL, as git's own check does
STATE_NAME = "git-resolve.json"   # file name under the git dir
LABEL_MAX = 40               # marker labels are cut to this many characters
DISPLAY_RULE = "=" * 80      # separator above each conflict display
SUBMODULE_MODE = "160000"    # git's file mode for a gitlink (submodule)

# Operations whose conflicts are concluded by 'git commit', and the file
# under the git dir that names the other side's commit.
COMMIT_OPS = {"merge": "MERGE_HEAD", "cherry-pick": "CHERRY_PICK_HEAD",
              "revert": "REVERT_HEAD"}

BLOCK_HELP = textwrap.dedent("""\
      1  keep ours (the side you were on)
      2  keep theirs (the side being merged in)
      b  keep both: ours, then theirs
      e  edit the block in your git editor; what you save is kept
      t  type the replacement lines; end with a line holding only '.'
      s  skip: leave this block marked, decide later
      q  quit: save what is decided so far and stop""")

WHOLE_HELP = textwrap.dedent("""\
      1  take ours
      2  take theirs
      k  keep the working copy as it is
      s  skip: leave the file unmerged, decide later
      q  quit: save what is decided so far and stop""")


# ---- errors -----------------------------------------------------------------
class RunError(Exception):
    """An expected failure, reported as ``git-resolve: error: <message>``.

    Attributes:
        detail: Extra output (such as git's stderr) printed before the error
            line, or an empty string.
    """

    def __init__(self, message: str, detail: str = "") -> None:
        """Store the message and optional detail.

        Args:
            message: One line, lower-case start, no final period.
            detail: Multi-line text to show above the error line.
        """
        super().__init__(message)
        self.detail = detail


class GitError(RunError):
    """A git command exited with a non-zero status."""


class MarkerError(Exception):
    """Conflict markers that do not form complete blocks.

    Attributes:
        line: 1-based line number where the problem was found.
    """

    def __init__(self, line: int, message: str) -> None:
        """Store where the markers went wrong.

        Args:
            line: 1-based line number of the offending line.
            message: What is wrong, e.g. "conflict not closed".
        """
        super().__init__(f"line {line}: {message}")
        self.line = line


# ---- data -------------------------------------------------------------------
@dataclass
class Block:
    """One conflict block, exactly as git wrote it.

    Attributes:
        start: 1-based line number of the ``<<<<<<<`` line.
        end: 1-based line number of the ``>>>>>>>`` line.
        ours_label: Text after ``<<<<<<<`` (usually HEAD).
        base_label: Text after ``|||||||`` (diff3/zdiff3 style only).
        theirs_label: Text after ``>>>>>>>`` (the branch or commit merged).
        ours: Lines of our side, each with its line ending.
        base: Lines of the common ancestor, or None without diff3 markers.
        theirs: Lines of their side, each with its line ending.
        raw: Every line of the block, markers included, for writing an
            undecided block back unchanged.
        resolution: The lines chosen to replace the block; None while
            undecided.
    """

    start: int
    end: int = 0
    ours_label: str = ""
    base_label: str = ""
    theirs_label: str = ""
    ours: list[str] = field(default_factory=list)
    base: list[str] | None = None
    theirs: list[str] = field(default_factory=list)
    raw: list[str] = field(default_factory=list)
    resolution: list[str] | None = None


@dataclass
class Conflict:
    """One unmerged path and what kind of conflict it holds.

    Attributes:
        path: Path relative to the top of the work tree.
        stages: Index stages present for the path, mapped to their file
            mode: 1 base, 2 ours, 3 theirs.
        kind: "text" when it holds marker blocks; otherwise a whole-file
            reason such as "binary" or "deleted by them".
        parts: For "text": plain lines as (line number, text) tuples and
            Block objects, in file order. None for other kinds.
        eol: The file's dominant line ending, used for lines this tool adds.
        error: Why the markers could not be read (kind "bad markers").
    """

    path: str
    stages: dict[int, str]
    kind: str
    parts: list[Any] | None = None
    eol: str = "\n"
    error: str = ""

    @property
    def blocks(self) -> list[Block]:
        """The marker blocks of a text conflict, in file order."""
        return [p for p in self.parts or [] if isinstance(p, Block)]


@dataclass
class Decision:
    """What was kept for one block or one whole file.

    Attributes:
        path: Path relative to the top of the work tree.
        block: 1-based block number; 0 for a whole-file decision.
        total: Number of blocks the file had in this run; 0 for whole files.
        lines: Line range of the block before resolution, e.g. "40-52".
        choice: Machine-readable key: ours, theirs, both, edited, typed,
            keep.
        text: Human-readable description, e.g. "kept ours (HEAD)".
    """

    path: str
    block: int
    total: int
    lines: str
    choice: str
    text: str

    def line(self) -> str:
        """Format the decision as one commit-message bullet.

        Returns:
            For example ``- src/app.c #1 of 2 (lines 40-52): kept ours (HEAD)``.
        """
        if self.block:
            return (f"- {self.path} #{self.block} of {self.total} "
                    f"(lines {self.lines}): {self.text}")
        return f"- {self.path}: {self.text}"


# ---- terminal ---------------------------------------------------------------
class Ui:
    """Everything written to stderr: displays, prompts, progress, warnings.

    Attributes:
        color: Whether ANSI colors are written.
        quiet: Whether progress messages are suppressed (--quiet).
        verbose: Verbosity count (--verbose); 1 echoes git commands.
    """

    STYLES = {"bold": "1", "dim": "2", "ours": "32", "theirs": "36",
              "warn": "33"}

    def __init__(self, color: bool, quiet: bool, verbose: int) -> None:
        """Set the output style.

        Args:
            color: Write ANSI colors.
            quiet: Suppress progress messages; errors and prompts still show.
            verbose: 0 normal, 1 or more echoes each git command.
        """
        self.color, self.quiet, self.verbose = color, quiet, verbose

    def paint(self, text: str, style: str) -> str:
        """Wrap text in an ANSI color when color is on.

        Args:
            text: The text to color.
            style: A key of STYLES.

        Returns:
            The text, colored or unchanged.
        """
        if not self.color:
            return text
        return f"\033[{self.STYLES[style]}m{text}\033[0m"

    def show(self, text: str) -> None:
        """Write a display line (conflict views, help); never suppressed.

        Args:
            text: One or more lines, without the final newline.
        """
        sys.stderr.write(text + "\n")

    def info(self, text: str) -> None:
        """Write a progress message unless --quiet.

        Args:
            text: The message, without the final newline.
        """
        if not self.quiet:
            sys.stderr.write(text + "\n")

    def debug(self, text: str) -> None:
        """Write a message only with --verbose.

        Args:
            text: The message, without the final newline.
        """
        if self.verbose:
            sys.stderr.write(self.paint(text, "dim") + "\n")

    def warn(self, text: str) -> None:
        """Write ``git-resolve: warning: <text>``; never suppressed.

        Args:
            text: The warning, lower-case start, no final period.
        """
        sys.stderr.write(self.paint(f"{APP_NAME}: warning: {text}", "warn")
                         + "\n")

    def ask(self, prompt: str) -> str:
        """Prompt on stderr and read one line from stdin.

        Args:
            prompt: The prompt text, written without a newline.

        Returns:
            The answer without its line ending.

        Raises:
            EOFError: stdin was closed; callers treat this as quit.
        """
        sys.stderr.write(self.paint(prompt, "bold"))
        sys.stderr.flush()
        line = sys.stdin.readline()
        if line == "":
            sys.stderr.write("\n")
            raise EOFError
        return line.rstrip("\r\n")


def want_color(when: str) -> bool:
    """Decide whether to color the stderr displays.

    The command line wins over the environment: ``--color always`` colors
    even with NO_COLOR set; ``auto`` needs a terminal and no NO_COLOR.

    Args:
        when: The --color value: auto, always or never.

    Returns:
        True when colors should be written.
    """
    if when != "auto":
        return when == "always"
    return (sys.stderr.isatty() and not os.environ.get("NO_COLOR")
            and os.environ.get("TERM") != "dumb")


# ---- git --------------------------------------------------------------------
class Git:
    """Runs git commands, by default in one directory.

    Attributes:
        cwd: Directory commands run in unless a call says otherwise.
        ui: Where --verbose echoes the commands.
    """

    def __init__(self, cwd: Path, ui: Ui) -> None:
        """Remember the default directory and the output channel.

        Args:
            cwd: Default working directory for git commands.
            ui: Terminal output, for --verbose echoes.
        """
        self.cwd, self.ui = cwd, ui

    def run(self, *args: str, cwd: Path | None = None, check: bool = True,
            stdin: bytes | None = None,
            passthrough: bool = False) -> subprocess.CompletedProcess:
        """Run one git command.

        Args:
            *args: Arguments after ``git``.
            cwd: Directory to run in; None means self.cwd.
            check: Raise GitError when git exits non-zero.
            stdin: Bytes fed to git's stdin, or None for no input.
            passthrough: Send git's output to our stderr instead of
                capturing it (for commit hooks and push progress).

        Returns:
            The finished process; stdout and stderr are bytes when captured.

        Raises:
            GitError: git is missing, or exited non-zero with check set.
        """
        cmd = ["git", *args]
        self.ui.debug("+ " + " ".join(shlex.quote(a) for a in cmd))
        out = sys.stderr if passthrough else subprocess.PIPE
        try:
            r = subprocess.run(cmd, cwd=cwd or self.cwd, input=stdin,
                               stdout=out,
                               stderr=None if passthrough else subprocess.PIPE)
        except FileNotFoundError:
            raise GitError("cannot run git: not found on PATH (install git)") \
                from None
        if check and r.returncode != 0:
            err = (r.stderr or b"").decode("utf-8", "replace").strip()
            lines = err.splitlines()
            why = lines[-1] if lines else f"exit status {r.returncode}"
            raise GitError(f"git {args[0]} failed: {why}",
                           "\n".join(lines[:-1]))
        return r

    def out(self, *args: str, cwd: Path | None = None,
            check: bool = True) -> str:
        """Run git and return its stdout as stripped text.

        Args:
            *args: Arguments after ``git``.
            cwd: Directory to run in; None means self.cwd.
            check: Raise GitError when git exits non-zero.

        Returns:
            Standard output, decoded and stripped; empty on failure when
            check is False.
        """
        r = self.run(*args, cwd=cwd, check=check)
        if r.returncode != 0:
            return ""
        return os.fsdecode(r.stdout).strip()


def git_path(git: Git, top: Path, name: str) -> Path:
    """Locate a file inside the git dir (works for worktrees too).

    Args:
        git: Command runner.
        top: Top of the work tree.
        name: Name under the git dir, e.g. "MERGE_HEAD".

    Returns:
        The absolute path; it may not exist.
    """
    return top / git.out("rev-parse", "--git-path", name, cwd=top)


def operation(git: Git, top: Path) -> str | None:
    """Find which git operation left the conflicts.

    A rebase is checked first, because an interactive rebase that stops on a
    'pick' can also leave CHERRY_PICK_HEAD behind.

    Args:
        git: Command runner.
        top: Top of the work tree.

    Returns:
        "rebase", "merge", "cherry-pick", "revert", or None (for example
        conflicts from 'git stash pop').
    """
    for d in ("rebase-merge", "rebase-apply"):
        if git_path(git, top, d).is_dir():
            return "rebase"
    for op, head in COMMIT_OPS.items():
        if git_path(git, top, head).is_file():
            return op
    return None


def unmerged(git: Git, cwd: Path, top: Path,
             paths: Sequence[str]) -> dict[str, dict[int, str]]:
    """List unmerged paths with the index stages git holds for each.

    Args:
        git: Command runner.
        cwd: Directory the user's <path> arguments are relative to.
        top: Top of the work tree.
        paths: Pathspecs to limit the search; empty means the whole repo.

    Returns:
        Path relative to top -> {stage number: file mode}, sorted by path.
    """
    if paths:
        r = git.run("ls-files", "-u", "-z", "--full-name", "--", *paths,
                    cwd=cwd)
    else:
        r = git.run("ls-files", "-u", "-z", cwd=top)
    files: dict[str, dict[int, str]] = {}
    for rec in r.stdout.split(b"\0"):
        if not rec:
            continue
        meta, _, name = rec.partition(b"\t")
        mode, _sha, stage = meta.decode("ascii").split()
        files.setdefault(os.fsdecode(name), {})[int(stage)] = mode
    return dict(sorted(files.items()))


# ---- markers ----------------------------------------------------------------
def split_lines(text: str) -> list[str]:
    r"""Split on newlines only, keeping each line's ending.

    str.splitlines() also splits on form feeds and other separators, which
    would shift line numbers in files that contain them.

    Args:
        text: File contents.

    Returns:
        Lines with their "\n" (and "\r" when CRLF); the last line has no
        ending when the file does not end with one.
    """
    lines = [ln + "\n" for ln in text.split("\n")]
    lines[-1] = lines[-1][:-1]
    if not lines[-1]:
        lines.pop()
    return lines


def bare(line: str) -> str:
    """Return a line without its line ending (LF or CRLF).

    Args:
        line: One line, with or without an ending.

    Returns:
        The line content only.
    """
    return line.rstrip("\r\n")


def parse_markers(text: str) -> list[Any]:
    """Split a file into plain lines and conflict blocks.

    Handles both the default style and diff3/zdiff3 (with a ``|||||||``
    base section). Inside the theirs section a ``=======`` line is content.

    Args:
        text: File contents.

    Returns:
        Plain lines as (1-based line number, text) tuples and Block objects,
        in file order.

    Raises:
        MarkerError: Markers that do not form complete blocks.
    """
    parts: list[Any] = []
    block: Block | None = None
    section = ""
    for n, line in enumerate(split_lines(text), 1):
        s = bare(line)
        if block is None:
            m = START_RE.match(s)
            if m:
                block = Block(start=n, ours_label=(m.group(1) or "").strip(),
                              raw=[line])
                section = "ours"
            elif END_RE.match(s):
                raise MarkerError(n, "'>>>>>>>' with no '<<<<<<<' before it")
            else:
                parts.append((n, line))
            continue
        block.raw.append(line)
        if START_RE.match(s):
            raise MarkerError(n, "'<<<<<<<' inside another conflict")
        m = BASE_RE.match(s)
        if m and section == "ours":
            block.base, block.base_label = [], (m.group(1) or "").strip()
            section = "base"
            continue
        if SEP_RE.match(s) and section in ("ours", "base"):
            section = "theirs"
            continue
        m = END_RE.match(s)
        if m and section == "theirs":
            block.end, block.theirs_label = n, (m.group(1) or "").strip()
            parts.append(block)
            block = None
            continue
        if m:
            raise MarkerError(n, "'>>>>>>>' before '======='")
        getattr(block, section).append(line)
    if block is not None:
        raise MarkerError(block.start, "conflict not closed by '>>>>>>>'")
    return parts


def has_markers(lines: Sequence[str]) -> bool:
    """Tell whether text still holds a start or end marker line.

    Args:
        lines: Lines to scan.

    Returns:
        True if any line is a ``<<<<<<<`` or ``>>>>>>>`` marker.
    """
    return any(START_RE.match(bare(ln)) or END_RE.match(bare(ln))
               for ln in lines)


def concat(first: Sequence[str], second: Sequence[str],
           eol: str) -> list[str]:
    """Join two sides, making sure the first ends with a line ending.

    Args:
        first: Lines kept first.
        second: Lines kept after them.
        eol: Line ending to add when the first side's last line has none.

    Returns:
        The combined lines.
    """
    out = list(first)
    if out and not out[-1].endswith("\n"):
        out[-1] += eol
    return out + list(second)


def render(parts: Sequence[Any], eol: str) -> str:
    """Rebuild a file from its parts: decided blocks replaced, others as-is.

    Args:
        parts: Output of parse_markers, with resolutions filled in.
        eol: Line ending added when a resolution's last line has none and
            more of the file follows.

    Returns:
        The new file contents.
    """
    out: list[str] = []
    for i, p in enumerate(parts):
        if isinstance(p, Block):
            chosen = list(p.raw if p.resolution is None else p.resolution)
            if chosen and not chosen[-1].endswith("\n") and i < len(parts) - 1:
                chosen[-1] += eol
            out += chosen
        else:
            out.append(p[1])
    return "".join(out)


def complete_block_starts(lines: Sequence[str]) -> list[int]:
    """Find leftover marker blocks, for --check.

    Only a complete ``<<<<<<<`` ... ``=======`` ... ``>>>>>>>`` sequence
    counts, so Markdown or reST underlines of '=' are never reported.

    Args:
        lines: Lines of one file.

    Returns:
        1-based line numbers of each block's ``<<<<<<<`` line.
    """
    hits, i = [], 0
    while i < len(lines):
        if START_RE.match(bare(lines[i])):
            sep = end = None
            for j in range(i + 1, len(lines)):
                s = bare(lines[j])
                if START_RE.match(s):
                    break
                if sep is None and SEP_RE.match(s):
                    sep = j
                elif sep is not None and END_RE.match(s):
                    end = j
                    break
            if end is not None:
                hits.append(i + 1)
                i = end
        i += 1
    return hits


def decode(data: bytes) -> str:
    """Decode file bytes so that writing them back is lossless.

    Args:
        data: Raw file contents in any encoding.

    Returns:
        Text; bytes that are not UTF-8 become surrogate escapes.
    """
    return data.decode("utf-8", "surrogateescape")


def encode(text: str) -> bytes:
    """Encode text produced by decode() back to the original bytes.

    Args:
        text: Text from decode(), possibly edited.

    Returns:
        Bytes, with surrogate escapes restored to their original values.
    """
    return text.encode("utf-8", "surrogateescape")


def classify(top: Path, path: str, stages: dict[int, str]) -> Conflict:
    """Work out what kind of conflict one unmerged path holds.

    Args:
        top: Top of the work tree.
        path: Path relative to top.
        stages: Index stages present for the path -> file mode.

    Returns:
        A Conflict; kind "text" has its parts parsed.
    """
    c = Conflict(path, stages, "text")
    f = top / path
    if SUBMODULE_MODE in stages.values():
        c.kind = "submodule"
    elif 2 not in stages and 3 not in stages:
        c.kind = "deleted by both"
    elif 2 not in stages:
        c.kind = "deleted by us"
    elif 3 not in stages:
        c.kind = "deleted by them"
    elif f.is_symlink():
        c.kind = "symlink"
    elif not f.is_file():
        c.kind = "missing from the working tree"
    else:
        data = f.read_bytes()
        if b"\0" in data[:BINARY_SNIFF]:
            c.kind = "binary"
            return c
        text = decode(data)
        c.eol = "\r\n" if text.count("\r\n") * 2 > text.count("\n") else "\n"
        try:
            c.parts = parse_markers(text)
        except MarkerError as e:
            c.kind, c.error = "bad markers", str(e)
            return c
        if not c.blocks:
            c.kind = "no markers left"
    return c


def plural(lines: Sequence[str]) -> str:
    """Say how many lines there are, e.g. "1 line" or "3 lines".

    Args:
        lines: The lines to count.

    Returns:
        The count with the right noun.
    """
    return f"{len(lines)} line" + ("" if len(lines) == 1 else "s")


def short_label(label: str, fallback: str) -> str:
    """Shorten a marker label for display and decision text.

    Args:
        label: Text after the marker, e.g. "HEAD" or "a1b2c3d (Add login)".
        fallback: Used when the label is empty, e.g. "ours".

    Returns:
        The label, cut to LABEL_MAX characters with "..." when longer.
    """
    label = label or fallback
    return label if len(label) <= LABEL_MAX else label[:LABEL_MAX - 3] + "..."


# ---- files ------------------------------------------------------------------
def write_file(path: Path, text: str) -> None:
    """Replace a file's contents atomically, keeping its permissions.

    Args:
        path: The file to replace.
        text: New contents, as produced by decode()/render().

    Raises:
        OSError: The folder is not writable.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".git-resolve-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(encode(text))
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def state_key(git: Git, top: Path, op: str | None) -> str | None:
    """Identify the operation in progress, so saved decisions match it.

    Args:
        git: Command runner.
        top: Top of the work tree.
        op: Result of operation().

    Returns:
        "<op>:<HEAD>:<other head>" for a merge, cherry-pick or revert;
        None otherwise (nothing is saved then).
    """
    if op not in COMMIT_OPS:
        return None
    head = git.out("rev-parse", "-q", "--verify", "HEAD", cwd=top, check=False)
    try:
        other = git_path(git, top, COMMIT_OPS[op]).read_text().split()
    except OSError:
        return None
    return f"{op}:{head}:{' '.join(other)}"


def load_state(path: Path, key: str | None) -> list[Decision]:
    """Read decisions saved by an earlier run of the same operation.

    Args:
        path: The state file.
        key: Result of state_key(); None means nothing can match.

    Returns:
        The saved decisions, or [] when there are none or they belong to
        another operation.
    """
    if key is None:
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("key") != key:
            return []
        return [Decision(**d) for d in data["decisions"]]
    except (OSError, ValueError, TypeError, KeyError):
        return []


def save_state(path: Path, key: str | None,
               decisions: Sequence[Decision]) -> None:
    """Save decisions so a later run's commit message includes them.

    Args:
        path: The state file.
        key: Result of state_key(); None saves nothing.
        decisions: Every decision so far, earlier runs included.
    """
    if key is None or not decisions:
        return
    text = json.dumps({"key": key, "decisions": [asdict(d) for d in decisions]},
                      indent=2)
    path.write_text(text + "\n", encoding="utf-8")


# ---- one run ----------------------------------------------------------------
QUIT = "quit"   # sentinel returned by the prompt helpers


@dataclass
class Run:
    """State of one invocation.

    Attributes:
        args: Parsed command line.
        ui: Terminal output.
        git: Command runner.
        top: Top of the work tree.
        decisions: Decisions made in this run.
        resolved: Paths fully resolved in this run.
        unresolved: Paths left unmerged in this run.
        quit: The user chose q (or stdin closed).
    """

    args: argparse.Namespace
    ui: Ui
    git: Git
    top: Path
    decisions: list[Decision] = field(default_factory=list)
    resolved: set[str] = field(default_factory=set)
    unresolved: list[str] = field(default_factory=list)
    quit: bool = False

    def stage(self, *cmd: str) -> None:
        """Run a staging command (add, rm, checkout) unless --dry-run.

        Args:
            *cmd: Arguments after ``git``.
        """
        if self.args.dry_run:
            self.ui.info("would run: git " + " ".join(shlex.quote(a)
                                                       for a in cmd))
        else:
            self.git.run(*cmd, cwd=self.top)


def show_block(r: Run, c: Conflict, index: int, idx: int, total: int) -> None:
    """Display one conflict block with context and numbered sides.

    Args:
        r: The run.
        c: The file the block is in.
        index: Position of the block in c.parts.
        idx: 1-based block number shown to the user.
        total: Number of blocks in the file.
    """
    ui, parts, n = r.ui, c.parts or [], r.args.context
    b: Block = parts[index]
    before: list[tuple[int, str]] = []
    i = index - 1
    while i >= 0 and len(before) < n and isinstance(parts[i], tuple):
        before.insert(0, parts[i])
        i -= 1
    after: list[tuple[int, str]] = []
    i = index + 1
    while i < len(parts) and len(after) < n and isinstance(parts[i], tuple):
        after.append(parts[i])
        i += 1

    def rows(lines: Sequence[str], first: int, style: str | None) -> None:
        if not lines:
            ui.show("         " + ui.paint("(nothing)", "dim"))
        for k, ln in enumerate(lines):
            text = f"{first + k:>6} | {bare(ln)}"
            ui.show(ui.paint(text, style) if style else text)

    ui.show(ui.paint(DISPLAY_RULE, "bold"))
    ui.show(ui.paint(f"{c.path}: conflict {idx} of {total}, "
                     f"lines {b.start}-{b.end}", "bold"))
    ui.show(ui.paint(DISPLAY_RULE, "bold"))
    for ln_no, text in before:
        ui.show(f"{ln_no:>6} | {bare(text)}")
    line = b.start + 1
    ui.show(ui.paint(f"  1) ours: {short_label(b.ours_label, 'ours')}",
                     "ours"))
    rows(b.ours, line, "ours")
    line += len(b.ours)
    if b.base is not None:
        line += 1
        ui.show(ui.paint(f"     base: {short_label(b.base_label, 'base')}",
                         "dim"))
        rows(b.base, line, "dim")
        line += len(b.base)
    line += 1
    ui.show(ui.paint(f"  2) theirs: {short_label(b.theirs_label, 'theirs')}",
                     "theirs"))
    rows(b.theirs, line, "theirs")
    ui.show("  " + "-" * 40)
    for ln_no, text in after:
        ui.show(f"{ln_no:>6} | {bare(text)}")


def edit_lines(r: Run, c: Conflict, text: str) -> list[str] | None:
    """Open text in the git editor and return what the user saved.

    The temporary file keeps the conflicted file's extension so the editor
    picks the right syntax highlighting. The editor is the one git uses
    (``git var GIT_EDITOR``), run through sh as git does.

    Args:
        r: The run.
        c: The file being resolved.
        text: Initial contents: the block with its markers.

    Returns:
        The saved lines, or None when the editor failed or markers remain
        (the user is asked again).
    """
    editor = r.git.out("var", "GIT_EDITOR", cwd=r.top)
    fd, tmp = tempfile.mkstemp(prefix="git-resolve-",
                               suffix=Path(c.path).suffix or ".txt")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(encode(text))
        done = subprocess.run(["sh", "-c", editor + ' "$@"', editor, tmp])
        if done.returncode != 0:
            r.ui.warn(f"editor exited with status {done.returncode}; "
                      "choose again")
            return None
        lines = split_lines(decode(Path(tmp).read_bytes()))
    finally:
        Path(tmp).unlink(missing_ok=True)
    if has_markers(lines):
        r.ui.warn("the edited text still has conflict markers; choose again")
        return None
    return lines


def type_lines(r: Run, eol: str) -> list[str]:
    """Read replacement lines typed on stdin, up to a line holding '.'.

    Args:
        r: The run.
        eol: Line ending given to each typed line.

    Returns:
        The typed lines (possibly none).
    """
    r.ui.show("Type the replacement lines. End with a line holding only '.'")
    lines: list[str] = []
    while True:
        line = sys.stdin.readline()
        if line == "" or line.rstrip("\r\n") == ".":
            return lines
        lines.append(line.rstrip("\r\n") + eol)


def ask_block(r: Run, c: Conflict, b: Block, idx: int,
              total: int) -> tuple[list[str], str, str] | str | None:
    """Ask what to keep for one block until the answer is usable.

    Args:
        r: The run.
        c: The file the block is in.
        b: The block.
        idx: 1-based block number.
        total: Number of blocks in the file.

    Returns:
        (lines, choice key, decision text) for a decision, None to skip, or
        QUIT.
    """
    ours = short_label(b.ours_label, "ours")
    theirs = short_label(b.theirs_label, "theirs")
    while True:
        try:
            a = r.ui.ask(f"[{idx}/{total}] keep 1 ours, 2 theirs, b both, "
                         "e edit, t type, s skip, q quit, ? help: ")
        except EOFError:
            return QUIT
        a = a.strip().lower()
        if a == "1":
            return b.ours, "ours", f"kept ours ({ours})"
        if a == "2":
            return b.theirs, "theirs", f"kept theirs ({theirs})"
        if a == "b":
            return (concat(b.ours, b.theirs, c.eol), "both",
                    f"kept both (ours {ours}, then theirs {theirs})")
        if a == "e":
            lines = edit_lines(r, c, "".join(b.raw))
            if lines is not None:
                return lines, "edited", f"edited by hand ({plural(lines)})"
            continue
        if a == "t":
            lines = type_lines(r, c.eol)
            return lines, "typed", f"replaced with typed text ({plural(lines)})"
        if a == "s":
            return None
        if a == "q":
            return QUIT
        r.ui.show(BLOCK_HELP)


def take_block(b: Block, side: str, eol: str) -> tuple[list[str], str, str]:
    """Decide one block with --take, without asking.

    Args:
        b: The block.
        side: ours, theirs or both.
        eol: The file's line ending.

    Returns:
        (lines, choice key, decision text).
    """
    ours = short_label(b.ours_label, "ours")
    theirs = short_label(b.theirs_label, "theirs")
    if side == "ours":
        return b.ours, "ours", f"kept ours ({ours}) [--take ours]"
    if side == "theirs":
        return b.theirs, "theirs", f"kept theirs ({theirs}) [--take theirs]"
    return (concat(b.ours, b.theirs, eol), "both",
            f"kept both (ours {ours}, then theirs {theirs}) [--take both]")


def resolve_text(r: Run, c: Conflict) -> None:
    """Resolve every block of one text file, then write and stage it.

    The file is written when at least one block was decided; undecided
    blocks keep their markers. It is staged only when no block is left.

    Args:
        r: The run.
        c: A conflict of kind "text".
    """
    parts = c.parts or []
    where = [i for i, p in enumerate(parts) if isinstance(p, Block)]
    total, changed = len(where), False
    for idx, index in enumerate(where, 1):
        b: Block = parts[index]
        if r.args.take:
            got: Any = take_block(b, r.args.take, c.eol)
        else:
            show_block(r, c, index, idx, total)
            got = ask_block(r, c, b, idx, total)
        if got == QUIT:
            r.quit = True
            break
        if got is None:
            continue
        b.resolution, choice, text = got
        r.decisions.append(Decision(c.path, idx, total, f"{b.start}-{b.end}",
                                    choice, text))
        changed = True
    done = all(parts[i].resolution is not None for i in where)
    if changed:
        if r.args.dry_run:
            r.ui.info(f"would write {c.path}")
        else:
            write_file(r.top / c.path, render(parts, c.eol))
    if done:
        r.stage("add", "--", c.path)
        r.resolved.add(c.path)
        if not r.args.dry_run:
            r.ui.info(f"staged {c.path}")
    else:
        r.unresolved.append(c.path)
        left = sum(parts[i].resolution is None for i in where)
        r.ui.info(f"{c.path}: {left} of {total} conflicts left; "
                  "it stays unmerged")


def resolve_whole(r: Run, c: Conflict) -> None:
    """Resolve a path that has no marker blocks by taking a whole side.

    Args:
        r: The run.
        c: A conflict whose kind is not "text".
    """
    if c.kind in ("submodule", "bad markers"):
        why = c.error or "submodule conflict"
        r.ui.warn(f"{c.path}: {why}; resolve it by hand")
        r.unresolved.append(c.path)
        return

    def side(stage: int) -> str:
        if stage not in c.stages:
            return "delete the file"
        return "keep our version (HEAD)" if stage == 2 \
            else "keep their version"

    if r.args.take:
        if c.kind == "no markers left":
            action = "keep"
        elif r.args.take == "both":
            r.ui.warn(f"{c.path}: {c.kind}: 'both' cannot apply to a whole "
                      "file; left unmerged")
            r.unresolved.append(c.path)
            return
        else:
            action = r.args.take
    else:
        ui = r.ui
        ui.show(ui.paint(DISPLAY_RULE, "bold"))
        ui.show(ui.paint(f"{c.path}: {c.kind}", "bold"))
        ui.show(ui.paint(DISPLAY_RULE, "bold"))
        ui.show(ui.paint(f"  1) ours: {side(2)}", "ours"))
        ui.show(ui.paint(f"  2) theirs: {side(3)}", "theirs"))
        ui.show("  k) keep the working copy as it is")
        action = ""
        while not action:
            try:
                a = ui.ask("keep 1 ours, 2 theirs, k working copy, s skip, "
                           "q quit, ? help: ").strip().lower()
            except EOFError:
                a = "q"
            action = {"1": "ours", "2": "theirs", "k": "keep", "s": "skip",
                      "q": QUIT}.get(a, "")
            if not action:
                ui.show(WHOLE_HELP)
        if action == QUIT:
            r.quit = True
        if action in (QUIT, "skip"):
            r.unresolved.append(c.path)
            return

    tag = f" [--take {r.args.take}]" if r.args.take else ""
    if action in ("ours", "theirs"):
        stage = 2 if action == "ours" else 3
        if stage in c.stages:
            r.stage("checkout", f"--{action}", "--", c.path)
            r.stage("add", "--", c.path)
            text = f"took {action}: their version" if stage == 3 \
                else "took ours: our version"
        else:
            r.stage("rm", "-q", "--", c.path)
            text = f"took {action}: file deleted"
    else:
        f = r.top / c.path
        if f.exists() or f.is_symlink():
            r.stage("add", "--", c.path)
            text = "kept the working copy"
        else:
            r.stage("rm", "-q", "--", c.path)
            text = "kept the deletion"
    r.decisions.append(Decision(c.path, 0, 0, "", action, text + tag))
    r.resolved.add(c.path)
    if not r.args.dry_run:
        r.ui.info(f"staged {c.path}")


def commit_message(r: Run, decisions: Sequence[Decision],
                   note: str) -> str:
    """Build the commit message: git's own subject plus every decision.

    Comment lines of MERGE_MSG (git's "# Conflicts:" list) are dropped,
    because 'git commit -F' would otherwise keep them.

    Args:
        r: The run.
        decisions: Every decision, earlier runs included.
        note: The user's note, or "".

    Returns:
        The full message, ending with a newline.
    """
    char = r.git.out("config", "--get", "core.commentChar", cwd=r.top,
                     check=False)
    char = char if char and char != "auto" else "#"
    head = ""
    msg = git_path(r.git, r.top, "MERGE_MSG")
    if msg.is_file():
        kept = [ln for ln in msg.read_text(errors="replace").splitlines()
                if not ln.startswith(char)]
        head = "\n".join(kept).strip()
    lines = [head or "Resolve merge conflicts", "",
             "Conflicts resolved with git-resolve:"]
    lines += [d.line() for d in decisions]
    if note:
        lines += ["", "Note: " + note]
    return "\n".join(lines) + "\n"


def push(r: Run) -> str:
    """Push the current branch, setting the upstream on first push.

    Args:
        r: The run.

    Returns:
        What was pushed to, e.g. "origin/main".

    Raises:
        RunError: HEAD is detached, or the push was rejected.
    """
    branch = r.git.out("symbolic-ref", "--short", "-q", "HEAD", cwd=r.top,
                       check=False)
    if not branch:
        raise RunError("cannot push: HEAD is detached (check out a branch, "
                       "then git push)")
    upstream = r.git.out("rev-parse", "--abbrev-ref", "--symbolic-full-name",
                         "@{u}", cwd=r.top, check=False)
    try:
        if upstream:
            r.git.run("push", cwd=r.top, passthrough=True)
            return upstream
        r.git.run("push", "--set-upstream", r.args.remote, branch,
                  cwd=r.top, passthrough=True)
        return f"{r.args.remote}/{branch}"
    except GitError as e:
        raise RunError("git push failed; the commit is made locally "
                       "(pull, then git push)", e.detail) from None


def push_target(r: Run) -> str:
    """Describe where push() would push, for prompts and --dry-run.

    Args:
        r: The run.

    Returns:
        The upstream (e.g. "origin/main"), or "<remote>/<branch>" when the
        branch has none yet.
    """
    up = r.git.out("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
                   cwd=r.top, check=False)
    branch = r.git.out("symbolic-ref", "--short", "-q", "HEAD", cwd=r.top,
                       check=False) or "HEAD"
    return up or f"{r.args.remote}/{branch}"


def finish(r: Run, op: str | None, decisions: Sequence[Decision],
           state: Path) -> dict[str, Any]:
    """Commit and push once nothing is left unmerged, as --finish allows.

    Args:
        r: The run.
        op: The operation in progress (operation()).
        decisions: Every decision, earlier runs included.
        state: The state file, deleted after a successful commit.

    Returns:
        {"committed": short sha or None, "pushed": target or None}.

    Raises:
        RunError: The commit or the push failed.
    """
    a, ui = r.args, r.ui
    result: dict[str, Any] = {"committed": None, "pushed": None}
    if a.finish == "stage":
        ui.info("all conflicts staged; not committing (--finish stage)")
        return result
    if op == "rebase":
        ui.info("rebase in progress: run 'git rebase --continue' to commit "
                "(or 'git am --continue')")
        return result
    if op is None:
        ui.info("no merge, cherry-pick or revert in progress: the files are "
                "staged, nothing to commit")
        return result
    interactive = sys.stdin.isatty() and not a.yes
    note = a.message or ""
    if not note and interactive:
        try:
            note = ui.ask("Note for the commit message (Enter for none): ")
        except EOFError:
            note = ""
        note = note.strip()
    msg = commit_message(r, decisions, note)
    pushing = a.finish == "push"
    where = push_target(r) if pushing else ""
    preview = ui.show if interactive or a.dry_run else ui.info
    preview(ui.paint("Commit message:", "bold"))
    preview(textwrap.indent(msg.rstrip("\n"), "  ", lambda _: True))
    if a.dry_run:
        ui.info("would commit" + (f" and push to {where}" if pushing else ""))
        return result
    if interactive:
        q = "Commit" + (f" and push to {where}" if pushing else "") + "? [Y/n] "
        try:
            yes = ui.ask(q).strip().lower() in ("", "y", "yes")
        except EOFError:
            yes = False
        if not yes:
            ui.info("not committed; the resolved files stay staged")
            return result
    r.git.run("commit", "-q", "--cleanup=whitespace", "-F", "-", cwd=r.top,
              stdin=encode(msg), passthrough=True)
    state.unlink(missing_ok=True)
    result["committed"] = r.git.out("rev-parse", "--short", "HEAD", cwd=r.top)
    ui.info(f"committed {result['committed']}")
    if pushing:
        result["pushed"] = push(r)
        ui.info(f"pushed to {result['pushed']}")
    return result


# ---- modes ------------------------------------------------------------------
def list_mode(r: Run, files: dict[str, dict[int, str]]) -> int:
    """Report unmerged files and their conflict counts (--list).

    Args:
        r: The run.
        files: Output of unmerged().

    Returns:
        EXIT_UNRESOLVED when any file is unmerged, else 0.
    """
    rows = []
    for path, stages in files.items():
        c = classify(r.top, path, stages)
        rows.append({"path": path, "conflicts": len(c.blocks),
                     "kind": c.kind})
    if r.args.json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            n = row["conflicts"] if row["kind"] == "text" else "-"
            kind = "" if row["kind"] == "text" else f" ({row['kind']})"
            print(f"{n:>4}  {row['path']}{kind}")
        if not rows:
            r.ui.info("no unmerged files")
    return EXIT_UNRESOLVED if rows else 0


def check_mode(r: Run) -> int:
    """Scan every tracked file for leftover marker blocks (--check).

    git grep narrows the search to files with a ``<<<<<<<`` line (binary
    files skipped); complete_block_starts() then confirms each block.

    Args:
        r: The run.

    Returns:
        EXIT_UNRESOLVED when markers are found, else 0.

    Raises:
        GitError: git grep failed.
    """
    # No <path>: the whole repository, wherever it was started. With paths:
    # git grep runs where they are relative to, and prints paths relative
    # to there, so the subfolder prefix is added back.
    where = r.git.cwd if r.args.paths else r.top
    g = r.git.run("grep", "-I", "-l", "-z", "-E", "-e", "^<{%d}" % MARKER_SIZE,
                  "--", *r.args.paths, cwd=where, check=False)
    if g.returncode not in (0, 1):
        raise GitError("git grep failed: "
                       + g.stderr.decode("utf-8", "replace").strip())
    hits = []
    prefix = r.git.out("rev-parse", "--show-prefix", cwd=where)
    for name in filter(None, g.stdout.split(b"\0")):
        rel = os.fsdecode(name)
        lines = split_lines(decode((where / rel).read_bytes()))
        for ln in complete_block_starts(lines):
            hits.append({"path": os.path.normpath(prefix + rel), "line": ln})
    if r.args.json:
        print(json.dumps(hits, indent=2))
    else:
        for h in hits:
            print(f"{h['path']}:{h['line']}: leftover conflict markers")
        if not hits:
            r.ui.info("no leftover conflict markers")
    return EXIT_UNRESOLVED if hits else 0


def report(r: Run, decisions: Sequence[Decision], remaining: Sequence[str],
           op: str | None, result: dict[str, Any]) -> None:
    """Print the run's results to stdout: text, or JSON with --json.

    Args:
        r: The run.
        decisions: Every decision, earlier runs included.
        remaining: Paths still unmerged.
        op: The operation in progress.
        result: Output of finish() (committed, pushed).
    """
    if r.args.json:
        print(json.dumps({"operation": op, "dry_run": r.args.dry_run,
                          "decisions": [asdict(d) for d in decisions],
                          "unresolved": list(remaining), **result}, indent=2))
        return
    if r.args.quiet:
        return
    if decisions:
        print(f"Decisions ({len(decisions)}):")
        for d in decisions:
            print("  " + d.line())
    if remaining:
        print(f"Still unmerged ({len(remaining)}):")
        for p in remaining:
            print(f"  {p}")
    if result.get("committed"):
        print(f"Committed {result['committed']}")
    if result.get("pushed"):
        print(f"Pushed to {result['pushed']}")


def run_main(args: argparse.Namespace) -> int:
    """Do the work the command line asks for.

    Args:
        args: Parsed command line.

    Returns:
        The exit code: 0, or EXIT_UNRESOLVED when conflicts remain.

    Raises:
        RunError: An expected failure (not a repository, git failed).
    """
    ui = Ui(want_color(args.color), args.quiet, args.verbose)
    cwd = Path(args.directory or ".").resolve()
    if not cwd.is_dir():
        raise RunError(f"cannot change to '{args.directory}': "
                       "no such directory")
    git = Git(cwd, ui)
    top_text = git.out("rev-parse", "--show-toplevel", check=False)
    if not top_text:
        raise RunError(f"not a git work tree: {cwd}")
    r = Run(args, ui, git, Path(top_text))
    if args.check:
        return check_mode(r)
    files = unmerged(git, cwd, r.top, args.paths)
    if args.list:
        return list_mode(r, files)

    op = operation(git, r.top)
    key = state_key(git, r.top, op)
    state = git_path(git, r.top, STATE_NAME)
    earlier = load_state(state, key)
    if not files and not earlier:
        ui.info("no unmerged files" + (" match" if args.paths else ""))
        report(r, [], [], op, {"committed": None, "pushed": None})
        return 0
    if files:
        tty = sys.stdin.isatty()
        if not args.take and not tty:
            raise RunError("stdin is not a terminal, so conflicts cannot be "
                           "asked about (use --take <side>)")
        commits = op in COMMIT_OPS and args.finish != "stage"
        if commits and not tty and not args.yes and not args.dry_run:
            raise RunError("stdin is not a terminal, so the commit cannot be "
                           "confirmed (use --yes, or --finish stage)")
        if op == "rebase":
            ui.info("rebase in progress: 'ours' is the branch being rebased "
                    "onto, 'theirs' is your commit")
        for path, stages in files.items():
            c = classify(r.top, path, stages)
            if c.kind == "text":
                resolve_text(r, c)
            else:
                resolve_whole(r, c)
            if not args.dry_run:
                save_state(state, key, earlier + r.decisions)
            if r.quit:
                break
    decisions = earlier + r.decisions
    everything = unmerged(git, cwd, r.top, [])
    remaining = [p for p in everything if p not in r.resolved]
    result: dict[str, Any] = {"committed": None, "pushed": None}
    if remaining:
        outside = [p for p in remaining if p not in files]
        if outside and not r.quit:
            ui.info(f"{len(outside)} unmerged file(s) outside the given "
                    "paths; not committing")
        ui.info("run git-resolve again to finish the remaining conflicts")
        report(r, decisions, remaining, op, result)
        return EXIT_UNRESOLVED
    result = finish(r, op, decisions, state)
    report(r, decisions, [], op, result)
    return 0


# ---- command line -----------------------------------------------------------
class Parser(argparse.ArgumentParser):
    """argparse with the standard error format and no built-in help."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Create the parser with argparse's own --help turned off.

        Args:
            *args: Passed to argparse.ArgumentParser.
            **kwargs: Passed to argparse.ArgumentParser.
        """
        kwargs.setdefault("add_help", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> NoReturn:
        """Print the standard three-line usage error and exit 2.

        Args:
            message: argparse's description of what was wrong.
        """
        sys.exit(usage_error(self.prog, message))


def count(text: str) -> int:
    """Parse a non-negative integer option value (--context).

    Args:
        text: The value as typed.

    Returns:
        The number.

    Raises:
        argparse.ArgumentTypeError: Not a whole number of 0 or more.
    """
    try:
        value = int(text)
    except ValueError:
        value = -1
    if value < 0:
        raise argparse.ArgumentTypeError(f"invalid count: '{text}'")
    return value


def make_parser() -> Parser:
    """Build the command-line parser. Option text: see HELP_FILES.

    Returns:
        The parser for ``git-resolve``.
    """
    p = Parser(prog=APP_NAME)
    p.add_argument("-V", "--version", action="version",
                   version=f"{APP_NAME} {APP_VERSION}")
    p.add_argument("-v", "--verbose", action="count", default=0)
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("-n", "--dry-run", action="store_true")
    p.add_argument("-y", "--yes", action="store_true")
    p.add_argument("--color", metavar="<when>", default="auto",
                   choices=("auto", "always", "never"))
    p.add_argument("--json", action="store_true")
    p.add_argument("-l", "--list", action="store_true")
    p.add_argument("--check", action="store_true")
    p.add_argument("--take", metavar="<side>",
                   choices=("ours", "theirs", "both"))
    p.add_argument("-m", "--message", metavar="<text>")
    p.add_argument("--finish", metavar="<step>", default="push",
                   choices=("stage", "commit", "push"))
    p.add_argument("--remote", metavar="<name>", default="origin")
    p.add_argument("--context", metavar="<n>", type=count, default=3)
    p.add_argument("-C", "--directory", metavar="<dir>")
    p.add_argument("paths", nargs="*", metavar="<path>")
    return p


# One parser per command level, keyed like the help pages (agreement test).
LEVELS = {APP_NAME: make_parser}


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: help first, then parse and run.

    Args:
        argv: Arguments without the program name; None means sys.argv.

    Returns:
        The exit code (see 'git-resolve --help', Exit status).
    """
    argv = sys.argv[1:] if argv is None else list(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except AttributeError:      # replaced by a test harness
            pass
    code = dispatch(argv)           # always first
    if code is not None:
        return code
    try:
        args = make_parser().parse_intermixed_args(argv)
        if args.list and args.check:
            return usage_error(APP_NAME, "--list and --check cannot be "
                               "used together")
        if args.take and (args.list or args.check):
            return usage_error(APP_NAME, "--take cannot be used with "
                               "--list or --check")
        return run_main(args)
    except KeyboardInterrupt:
        sys.stderr.write("\n")
        return EXIT_INTERRUPTED
    except RunError as e:
        if e.detail:
            sys.stderr.write(e.detail + "\n")
        sys.stderr.write(f"{APP_NAME}: error: {e}\n")
        return EXIT_FAILURE
    except OSError as e:
        sys.stderr.write(f"{APP_NAME}: error: {e.filename or 'file'}: "
                         f"{e.strerror or e}\n")
        return EXIT_FAILURE


# ---- help (pasted from help.py; regenerate, do not edit) ----
WIDTH = 80          # pages are rendered at 80 columns in every language
SIG_COL = 28        # widest option signature before the summary wraps below
RULE = "\n\n" + "=" * WIDTH + "\n\n"   # separator between pages in 'all'
EXIT_OK, EXIT_FAILURE, EXIT_USAGE, EXIT_INTERRUPTED = 0, 1, 2, 130
RESERVED_COMMANDS = {"help", "all"}

# Reserved common options: long -> (short, arg, summary). A tool that offers
# one of these behaviors uses exactly this spelling; no tool reuses the names
# or the short letters for anything else.
COMMON = {
    "--help": ("-h", "[<topic>]",
               "Show help; <topic> is a command, an option, or 'all'"),
    "--version": ("-V", "", "Print the program name and version, then exit"),
    "--verbose": ("-v", "", "Print more detail; repeat for more (-vv)"),
    "--quiet": ("-q", "", "Print only errors"),
    "--dry-run": ("-n", "", "Show what would be done without doing it"),
    "--force": ("-f", "", "Proceed without the usual safety checks"),
    "--yes": ("-y", "", "Answer yes to every confirmation prompt"),
    "--output": ("-o", "<file>", "Write output to <file> ('-' for stdout)"),
    "--config": ("-c", "<file>", "Read settings from <file>"),
    "--color": ("", "<when>", "Use color: auto, always or never"),
    "--json": ("", "", "Print machine-readable JSON instead of text"),
}
ALWAYS_COMMON = ("--help", "--version")

CMD_FIELDS = {"summary", "args", "hidden", "common"}
OPT_FIELDS = {"summary", "short", "arg", "default", "env", "choices",
              "repeat", "hidden", "scope"}
NAME_CMD = re.compile(r"[a-z][a-z0-9-]*$")
NAME_OPT = re.compile(r"--[a-z][a-z0-9-]*$")
SECTION = re.compile(r"^[A-Z][A-Za-z ]*:$")


@dataclass
class Opt:
    key: str
    long: str
    short: str = ""
    arg: str = ""
    summary: str = ""
    default: str = ""
    env: str = ""
    choices: str = ""
    repeat: bool = False
    hidden: bool = False
    scope: str = "command"
    body: str = ""

    @property
    def takes_value(self) -> bool:
        return self.arg.startswith("<")


@dataclass
class Cmd:
    key: str
    summary: str = ""
    args: str = ""
    arg_docs: list = field(default_factory=list)
    body: str = ""
    hidden: bool = False
    common: list = field(default_factory=list)
    children: list = field(default_factory=list)
    options: dict = field(default_factory=dict)


def parse_file(text: str):
    """Split a help file into (fields, arg_docs, body)."""
    lines = text.splitlines()
    cut = lines.index("---") if "---" in lines else len(lines)
    fields, arg_docs = {}, []
    for ln in lines[:cut]:
        if not ln.strip():
            continue
        k, sep, v = ln.partition(":")
        if not sep:
            raise ValueError(f"header line has no ':': {ln!r}")
        k, v = k.strip(), v.strip()
        if k.startswith("arg "):
            arg_docs.append((k[4:].strip(), v))
        else:
            fields[k] = v
    return fields, arg_docs, "\n".join(lines[cut + 1:]).strip("\n")


def table(rows):
    """Two-column list: names left, summaries wrapped on the right."""
    w = min(max(len(left) for left, _ in rows), SIG_COL)
    pad = " " * (2 + w + 2)
    out = []
    for left, right in rows:
        if len(left) > w:
            out.append("  " + left)
            first = pad
        else:
            first = "  " + left.ljust(w) + "  "
        out += textwrap.wrap(right, WIDTH, initial_indent=first,
                             subsequent_indent=pad, break_on_hyphens=False,
                             break_long_words=False) or [first.rstrip()]
    return out


def split_body(body: str):
    """Description = text before the first 'Heading:' line; the rest trails."""
    lines = body.splitlines()
    for i, ln in enumerate(lines):
        if SECTION.match(ln):
            return ("\n".join(lines[:i]).strip("\n"),
                    "\n".join(lines[i:]).strip("\n"))
    return body, ""


def distance(a: str, b: str) -> int:
    """Levenshtein distance for 'Did you mean'; 99 past 64 chars (as in C)."""
    if len(a) > 64 or len(b) > 64:
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


class HelpSet:
    """All help pages of one program, indexed by dotted key.

    Keys: 'mytool', 'mytool.build', 'mytool.build.--target'. Every lookup is
    a dict (hash) hit on a key built from the command path the user typed.
    """

    def __init__(self, files: dict):
        self.problems: list[str] = []
        self.cmds: dict[str, Cmd] = {}
        self.opts: dict[str, Opt] = {}
        self.index: dict[tuple, str] = {}   # (cmd key, '-t'|'--target') -> opt
        entries = sorted((n[:-4].split("."), n, t)
                         for n, t in files.items() if n.endswith(".txt"))
        roots = [segs[0] for segs, _, _ in entries if len(segs) == 1]
        if len(roots) != 1:
            raise ValueError("help/ needs exactly one root file <prog>.txt, "
                             f"found {len(roots)}")
        self.root = roots[0]
        for segs, name, text in entries:
            try:
                self._add(segs, text)
            except ValueError as e:
                self.problems.append(f"{name}: {e}")
        self._finish()

    def _add(self, segs, text):
        if segs[0] != self.root:
            raise ValueError(f"must start with '{self.root}.'")
        fields, arg_docs, body = parse_file(text)
        key = ".".join(segs)
        parent = ".".join(segs[:-1])
        if len(segs) > 1 and parent not in self.cmds:
            raise ValueError(f"no page for parent command '{parent}'")
        if segs[-1].startswith("-"):
            if not NAME_OPT.match(segs[-1]):
                raise ValueError("option names look like --lower-case")
            if arg_docs or set(fields) - OPT_FIELDS:
                raise ValueError("unknown fields "
                                 f"{sorted(set(fields) - OPT_FIELDS)}")
            o = Opt(key, segs[-1], fields.get("short", ""),
                    fields.get("arg", ""), fields.get("summary", ""),
                    fields.get("default", ""), fields.get("env", ""),
                    fields.get("choices", ""),
                    fields.get("repeat") == "yes",
                    fields.get("hidden") == "yes",
                    fields.get("scope", "command"), body)
            if o.long in COMMON:   # overriding a reserved option's text
                o.short, o.arg = COMMON[o.long][:2]
                o.scope = "global"
            self.opts[key] = o
            self.cmds[parent].options[o.long] = o
            return
        if len(segs) > 1 and not NAME_CMD.match(segs[-1]):
            raise ValueError("command names look like lower-case")
        if set(fields) - CMD_FIELDS:
            raise ValueError(f"unknown fields {sorted(set(fields) - CMD_FIELDS)}")
        c = Cmd(key, fields.get("summary", ""), fields.get("args", ""),
                arg_docs, body, fields.get("hidden") == "yes",
                fields.get("common", "").split())
        self.cmds[key] = c
        if len(segs) > 1:
            self.cmds[parent].children.append(key)
            c.hidden = c.hidden or self.cmds[parent].hidden

    def _finish(self):
        root = self.cmds.get(self.root)
        if root is None:
            raise ValueError("root page could not be read")
        for name in ALWAYS_COMMON + tuple(root.common):
            if name not in COMMON:
                self.problems.append(f"{self.root}.txt: '{name}' in common: "
                                     "is not a reserved common option")
            elif name not in root.options:
                short, arg, summary = COMMON[name]
                o = Opt(f"{self.root}.{name}", name, short, arg, summary,
                        scope="global")
                self.opts[o.key] = o
                root.options[name] = o
        rank = list(COMMON)
        self.glob = sorted((o for o in root.options.values()
                            if o.scope == "global"),
                           key=lambda o: (o.long not in COMMON,
                                          rank.index(o.long)
                                          if o.long in COMMON else 0, o.long))
        glob = self.glob
        self.order = []          # command keys, depth first (manual order)

        def walk(key):
            self.order.append(key)
            c = self.cmds[key]
            c.children.sort()
            mine = [o for o in c.options.values() if o.scope != "global"]
            for o in glob + mine:
                for tok in filter(None, (o.long, o.short)):
                    if (key, tok) in self.index:
                        self.problems.append(f"{key}: '{tok}' defined twice")
                    self.index[(key, tok)] = o.key
            for k in c.children:
                walk(k)
        walk(self.root)

    # ---- rendering --------------------------------------------------------
    def prog(self, key):
        return key.replace(".", " ")

    def usage_lines(self, key):
        c, p = self.cmds[key], self.prog(key)
        forms = []
        if c.children:
            forms.append(f"{p} [options] <command> [<args>]")
        if c.args or not c.children:
            forms.append(f"{p} [options]" + (f" {c.args}" if c.args else ""))
        out = []
        for n, form in enumerate(forms):
            out += textwrap.wrap(form, WIDTH,
                                 initial_indent="Usage: " if n == 0 else " " * 7,
                                 subsequent_indent=" " * (8 + len(p)),
                                 break_on_hyphens=False, break_long_words=False)
        return out

    @staticmethod
    def sig(o):
        s = (f"{o.short}, " if o.short else "    ") + o.long
        return s + (f" {o.arg}" if o.arg else "")

    @staticmethod
    def describe(o):
        extra = []
        if o.choices:
            extra.append("one of: " + ", ".join(o.choices.split("|")))
        if o.default:
            extra.append(f"default: {o.default}")
        if o.env:
            extra.append(f"env: {o.env}")
        if o.repeat:
            extra.append("repeatable")
        return o.summary + (f" ({'; '.join(extra)})" if extra else "")

    def page(self, key):
        return self.opt_page(key) if key in self.opts else self.cmd_page(key)

    def cmd_page(self, key):
        c, p = self.cmds[key], self.prog(key)
        out = [f"{p} - {c.summary}", ""] + self.usage_lines(key)
        desc, tail = split_body(c.body)
        if desc:
            out += ["", desc]
        if c.arg_docs:
            out += ["", "Arguments:"] + table(c.arg_docs)
        kids = [k for k in c.children if not self.cmds[k].hidden]
        if kids:
            out += ["", "Commands:"] + table(
                [(k.rsplit(".", 1)[1], self.cmds[k].summary) for k in kids])
        mine = [o for o in c.options.values()
                if o.scope != "global" and not o.hidden]
        if mine:
            out += ["", "Options:"] + table(
                [(self.sig(o), self.describe(o)) for o in mine])
        glob = [o for o in self.glob if not o.hidden]
        if key == self.root:
            out += ["", "Common options:"] + table(
                [(self.sig(o), self.describe(o)) for o in glob])
        else:
            names = ", ".join(o.short or o.long for o in glob)
            out += [""] + textwrap.wrap(
                f"Common options: {names} (see '{self.root} --help')", WIDTH,
                subsequent_indent="  ")
        what = "<command|option|all>" if kids else "<option|all>"
        out += ["", f"Run '{p} --help {what}' for more."]
        if tail:
            out += ["", tail]
        return "\n".join(out)

    def opt_page(self, key):
        o = self.opts[key]
        p = self.prog(key.rsplit(".", 1)[0])
        out = [f"{p} {o.long} - {o.summary}", ""]
        for n, form in enumerate(filter(None, (o.short, o.long))):
            out.append(("Usage: " if n == 0 else " " * 7) + f"{p} {form}"
                       + (f" {o.arg}" if o.arg else ""))
        facts = [("Choices", ", ".join(filter(None, o.choices.split("|")))),
                 ("Default", o.default), ("Environment", o.env),
                 ("Repeatable", "yes" if o.repeat else ""),
                 ("Scope", "every command" if o.scope == "global" else "")]
        facts = [f"{k + ':':<13}{v}" for k, v in facts if v]
        if facts:
            out += [""] + facts
        if o.body:
            out += ["", o.body]
        return "\n".join(out)

    def manual(self, key):
        keys = [k for k in self.order if k == key
                or (k.startswith(key + ".") and not self.cmds[k].hidden)]
        return RULE.join(self.cmd_page(k) for k in keys)

    # ---- the help request -------------------------------------------------
    def find_request(self, argv):
        """Return (command key, topic words) if argv asks for help, else None.

        The command path is every command name typed before the help flag,
        so 'mytool build --help' and 'mytool --help build' are the same page.
        """
        cur, positional, i = self.root, False, 0
        while i < len(argv):
            t = argv[i]
            rest = argv[i + 1:]
            rest = rest[:rest.index("--")] if "--" in rest else rest
            if t == "--":
                return None
            if t in ("-h", "--help"):
                return cur, rest
            if t.startswith("--help="):
                return cur, [t[7:]] + rest
            if t.startswith("--"):
                okey = self.index.get((cur, t.split("=", 1)[0]))
                if okey and self.opts[okey].takes_value and "=" not in t:
                    i += 1
            elif t.startswith("-") and t != "-":
                for j, ch in enumerate(t[1:], 1):
                    if ch == "h":
                        return cur, rest
                    okey = self.index.get((cur, "-" + ch))
                    if okey and self.opts[okey].takes_value:
                        i += j == len(t) - 1   # value is the next word
                        break
            elif not positional and self.cmds[cur].children:
                if t == "help":
                    return cur, rest
                if f"{cur}.{t}" in self.cmds:
                    cur = f"{cur}.{t}"
                else:
                    positional = True
            else:
                positional = True
            i += 1
        return None

    def resolve(self, cur, topic):
        """Return (text, exit code) for a help request."""
        words = []
        for t in topic:
            words += [t] if t.startswith("-") else t.split(".")
        if words and words[0] == self.root and cur == self.root \
                and f"{cur}.{self.root}" not in self.cmds:
            words = words[1:]
        for n, t in enumerate(words):
            last = n == len(words) - 1
            if t == "all" and last:
                return self.manual(cur), EXIT_OK
            if t.startswith("-"):
                okey = self.index.get((cur, t.split("=", 1)[0]))
                if okey and last:
                    return self.opt_page(okey), EXIT_OK
            elif f"{cur}.{t}" in self.cmds:
                cur = f"{cur}.{t}"
                continue
            return self.unknown(cur, t), EXIT_USAGE
        return self.cmd_page(cur), EXIT_OK

    def unknown(self, cur, word):
        c = self.cmds[cur]
        cands = [k.rsplit(".", 1)[1] for k in c.children
                 if not self.cmds[k].hidden]
        cands += sorted({tok for (k, tok) in self.index
                         if k == cur and tok.startswith("--")
                         and not self.opts[self.index[(k, tok)]].hidden})
        cands.append("all")
        near = sorted((distance(word, x), x) for x in cands)
        near = [x for d, x in near if d <= 2][:3]
        p = self.prog(cur)
        out = [f"{self.root}: no help topic '{word}' in '{p}'"]
        if near:
            out.append("Did you mean: " + ", ".join(near) + "?")
        out.append(f"Try '{p} --help' for the list of topics.")
        return "\n".join(out)

    def usage_error(self, command, message):
        """Standard usage error text for 'mytool build' (or 'build')."""
        words = command.split()
        if not words or words[0] != self.root:
            words = [self.root] + words
        key = ".".join(words)
        p = self.prog(key)
        usage = self.usage_lines(key) if key in self.cmds else []
        return "\n".join([f"{p}: error: {message}"] + usage
                         + [f"Try '{p} --help' for more information."])


# ---- runtime API -----------------------------------------------------------
_HELPSET = None
_APP = {"prog": None, "dir": None, "env": None}   # set by use_dir()


class HelpMissing(Exception):
    """The installed help files could not be read (app mode)."""


def env_name(prog: str) -> str:
    """'pbtool' -> 'PBTOOL_HELP_DIR' (non-alphanumerics become '_')."""
    return re.sub(r"[^A-Z0-9]", "_", prog.upper()) + "_HELP_DIR"


def missing_text(prog, where, env) -> str:
    return (f"{prog}: error: help files not found in '{where}' "
            f"(reinstall, or set {env})")


def use_dir(prog: str, help_dir=None) -> None:
    """App mode: read pages from files. <PROG>_HELP_DIR overrides help_dir;
    help_dir None means this package's help/ folder."""
    global _HELPSET
    env = env_name(prog)
    _APP.update(prog=prog, env=env,
                dir=os.environ.get(env) or help_dir)
    _HELPSET = None


def may_request_help(argv) -> bool:
    """Cheap pre-scan: False means argv surely asks for no help, so nothing
    is loaded (a missing help folder never breaks normal commands)."""
    for t in argv:
        if t == "--":
            return False
        if t in ("-h", "--help", "help") or t.startswith("--help="):
            return True
        if t.startswith("-") and not t.startswith("--") and "h" in t[1:]:
            return True
    return False


def read_dir(path) -> dict:
    return {p.name: p.read_text(encoding="ascii")
            for p in Path(path).iterdir() if p.name.endswith(".txt")}


def _package_help():
    if __package__:
        from importlib.resources import files as res
        return res(__package__) / "help"
    return Path(__file__).resolve().parent / "help"


def load() -> HelpSet:
    """Stand-alone: the embedded HELP_FILES. App: the help folder on disk
    (raises HelpMissing when it cannot be read)."""
    global _HELPSET
    if _HELPSET is None:
        files = globals().get("HELP_FILES")
        if not files:
            d = _APP["dir"] or _package_help()
            try:
                files = {p.name: p.read_text(encoding="ascii")
                         for p in Path(str(d)).iterdir()
                         if p.name.endswith(".txt")}
            except OSError:
                files = {}
            prog = _APP["prog"]
            if not files or (prog and f"{prog}.txt" not in files):
                raise HelpMissing(missing_text(prog or "help", d,
                                               _APP["env"] or "HELP_DIR"))
        _HELPSET = HelpSet(files)
    return _HELPSET


def dispatch(argv, hs: HelpSet | None = None):
    """Print help if argv asks for it and return the exit code, else None."""
    if hs is None:
        if not may_request_help(argv):
            return None
        try:
            hs = load()
        except HelpMissing as e:
            sys.stderr.write(f"{e}\n")
            return EXIT_FAILURE
    req = hs.find_request(list(argv))
    if req is None:
        return None
    text, code = hs.resolve(*req)
    (sys.stdout if code == EXIT_OK else sys.stderr).write(text + "\n")
    return code


def usage_error(command, message, hs: HelpSet | None = None) -> int:
    """Print the standard usage error to stderr and return 2."""
    try:
        hs = hs or load()
    except HelpMissing:          # still report the error, just no usage
        words = command.split()
        if not words or words[0] != _APP["prog"]:
            words = [_APP["prog"]] + words
        p = " ".join(words)
        sys.stderr.write(f"{p}: error: {message}\n"
                         f"Try '{p} --help' for more information.\n")
        return EXIT_USAGE
    sys.stderr.write(hs.usage_error(command, message) + "\n")
    return EXIT_USAGE


# generated by help.py gen from help/ -- do not edit
HELP_FILES = {
    'git-resolve.--check.txt': 'summary: Scan every tracked file for leftover conflict markers\n---\nFor CI: catches markers that were committed by mistake. A file counts\nonly when it holds a complete block (<<<<<<<, =======, >>>>>>>), so a\nMarkdown underline of \'=\' signs is not reported. Exits 3 when markers\nare found. With --json, prints a JSON array of {"path", "line"}.\n',
    'git-resolve.--context.txt': 'summary: Lines of context shown around each conflict\narg: <n>\ndefault: 3\n',
    'git-resolve.--directory.txt': 'summary: Run as if started in <dir>\nshort: -C\narg: <dir>\n',
    'git-resolve.--finish.txt': "summary: How far to go once every conflict is resolved\narg: <step>\nchoices: stage|commit|push\ndefault: push\n---\nstage   write and 'git add' the files only\ncommit  also commit, with the decisions in the message\npush    also push the branch (sets the upstream on first push)\n",
    'git-resolve.--list.txt': 'summary: List unmerged files and their conflict counts, then exit\nshort: -l\n---\nChanges nothing. Exits 3 when any file is unmerged, 0 when none is.\nWith --json, prints a JSON array of {"path", "conflicts", "kind"}.\n',
    'git-resolve.--message.txt': 'summary: Note to add to the commit message\nshort: -m\narg: <text>\n---\nGoes after the list of decisions. Without it, git-resolve asks for a\nnote when stdin is a terminal and --yes is not given.\n',
    'git-resolve.--remote.txt': 'summary: Remote to push to when the branch has no upstream yet\narg: <name>\ndefault: origin\n',
    'git-resolve.--take.txt': "summary: Resolve every block with one side, without asking\narg: <side>\nchoices: ours|theirs|both\n---\nFor scripts and CI. Whole-file conflicts take that side too; 'both'\ncannot apply to them, so they are left unmerged and the exit status is 3.\n",
    'git-resolve.txt': 'summary: Resolve conflict markers one by one, then commit and push\nargs: [<path>...]\narg <path>: Only resolve these files or folders (default: every one)\ncommon: --verbose --quiet --dry-run --yes --color --json\n---\nFinds every file git left unmerged, shows each conflict block with the\nlines around it, and asks what to keep:\n\n  1  ours    the side you were on (HEAD)\n  2  theirs  the side being merged in\n  b  both    ours, then theirs\n  e  edit    open the block in your git editor and keep what you save\n  t  type    type the replacement lines, end with a line holding \'.\'\n  s  skip    leave this block marked and come back later\n  q  quit    save what is decided so far and stop\n\nA file is written and staged with \'git add\' once its last block is\ndecided. When nothing is left unmerged, git-resolve commits with a\nmessage that lists every decision, then pushes. In a rebase it stages\nonly, because \'git rebase --continue\' makes the commit.\n\nFiles with no markers (binary files, or modified on one side and deleted\non the other) are offered as whole files: 1 ours, 2 theirs, or k to keep\nthe working copy as it is.\n\nExamples:\n  git-resolve\n  git-resolve src/app.c docs/\n  git-resolve --finish commit -m "kept the new login flow"\n  git-resolve --take theirs --yes --finish commit\n  git-resolve --list --json\n  git-resolve --check\n\nExit status:\n  0    Success, or nothing to resolve\n  1    A git command failed (commit hook, push rejected, not a repository)\n  2    Bad command line\n  3    Conflicts remain: blocks skipped, quit early, or found by --list\n       or --check\n  130  Interrupted (Ctrl-C)\n\nEnvironment:\n  GIT_EDITOR     Editor for \'e\' (else core.editor, VISUAL, EDITOR, vi)\n  NO_COLOR       Same as --color never\n\nFiles:\n  .git/git-resolve.json  Decisions made in an earlier run of the same\n                         merge, so the final commit message lists them\n                         all; removed after the commit\n',
}

if __name__ == "__main__":
    sys.exit(main())

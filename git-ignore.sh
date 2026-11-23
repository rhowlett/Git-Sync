#!/usr/bin/env bash
# v0.1
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
#  File      : git-ignore.sh
#  Copyright : (c) 2026 brickChip and Richard Howlett. All rights reserved.
#  Author    : Richard Howlett <rhowlett@eenerd.com>
#  License   : PolyForm Noncommercial 1.0.0. Free for noncommercial use;
#              commercial use requires written permission from the owner.
#              Full terms in LICENSE at the project root.
#  Warranty  : None. Provided as is; the owner is not liable for any
#              damages arising from its use (No Liability, LICENSE).
#  BRICKCHIP-ENTRY-POINT: Add a .gitignore pattern and untrack the files it matches
#  SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
################################################################################

# git-ignore.sh - add a pattern to .gitignore and stop tracking what it matches.
#
# Two jobs, which can be combined:
#   <pattern>  append the pattern to the .gitignore at the top of the repo
#              (unless it is already there), then untrack every tracked file
#              the pattern ignores.
#   --sync     untrack every tracked file that the repo's .gitignore files
#              ignore (the root one and any in subfolders).
# Untracking means 'git rm --cached': the files stay on disk and the removal
# is staged, ready to commit. Nothing is committed or pushed.
#
# Matching is done by git itself ('git ls-files -c -i'), so every pattern
# means exactly what it will mean in .gitignore: 'build/' matches a folder at
# any depth, '/build' only at the top, 'docs/build' only that path, '**' and
# '!' work, and nested .gitignore files apply below their own folder.
#
# --sync reads only .gitignore files, never .git/info/exclude or the user's
# global core.excludesFile. Those are personal; untracking because of them
# would delete the files from every other clone on the next pull.
#
# Option and help text lives in help_page() below, the one place it is
# written. Shell scripts cannot use the help.py runtime, so the page is laid
# out by hand like the standard help pages (cli-and-docs-conventions).
#
# Requires bash 3.2 or later (the bash macOS ships) and git 2.26 or later
# (for 'git rm --pathspec-from-file'). No bash 4 features are used: no
# mapfile, no associative arrays, no ${var,,}.

set -euo pipefail

PROG=$(basename "$0")
VERSION="0.1"

# Exit codes (cli-and-docs-conventions section 5).
EXIT_FAILURE=1        # a git command failed, or not inside a work tree
EXIT_USAGE=2          # bad command line
EXIT_INTERRUPTED=130  # Ctrl-C

GITIGNORE=".gitignore"   # the file patterns are added to, at the repo top

# ---- output -----------------------------------------------------------------

# help_page - print the help page to stdout.
#
# The page follows the standard layout: title, usage, description,
# Arguments, Options, Common options, then Examples, Exit status and Notes.
# Lines stay within 78 columns.
help_page() {
    cat <<EOF
$PROG - Add a pattern to .gitignore and untrack the files it matches

Usage: $PROG [options] <pattern>
       $PROG [options] --sync [<pattern>]

Appends <pattern> to the .gitignore at the top of the repository (unless it
is already there), then removes every tracked file the pattern matches from
git's index with 'git rm --cached'. The files stay on disk; the removal is
staged for you to review and commit. With --sync, every tracked file that
the repository's .gitignore files ignore is untracked instead.

Matching is done by git itself, so a pattern means exactly what it means in
.gitignore: 'build/' matches a build folder anywhere, '/build' only at the
top, 'docs/build' only that path.

Arguments:
  <pattern>  A .gitignore pattern, such as '*.log', 'build/' or '/.env'

Options:
  -s, --sync  Untrack every tracked file that the .gitignore files ignore

Common options:
  -h, --help     Show this help
  -V, --version  Print the program name and version, then exit
  -q, --quiet    Print only errors
  -n, --dry-run  Show what would be done without doing it

Examples:
  $PROG '.DS_Store'
  $PROG 'build/'
  $PROG --dry-run '*.log'
  $PROG --sync
  $PROG -sn

Exit status:
  0    Success, including when nothing matched
  1    Not inside a git work tree, or a git command failed
  2    Bad command line
  130  Interrupted (Ctrl-C)

Notes:
  The untracked paths are printed to stdout, one per line; progress goes
  to stderr. Patterns are added to the top-level .gitignore wherever in the
  repository you run this. Quote patterns so the shell does not expand them.

  When the commit reaches another clone, 'git pull' there deletes the files
  from that working tree, because they left the repository. Copy anything
  you still need on that machine before pulling.

  --sync reads only .gitignore files, not .git/info/exclude or your global
  excludes file, which are personal to this machine.
EOF
}

# say - write a progress message to stderr unless --quiet.
#
# Args:
#   $@  The message words.
say() {
    if [[ "$QUIET" == false ]]; then
        printf '%s\n' "$*" >&2
    fi
}

# die - report a runtime failure and exit 1.
#
# Format: "<prog>: error: <what>: <why> (<fix>)", lower-case, no final period.
#
# Args:
#   $@  The message words.
die() {
    printf '%s: error: %s\n' "$PROG" "$*" >&2
    exit "$EXIT_FAILURE"
}

# usage_error - report a bad command line in the standard three lines, exit 2.
#
# Args:
#   $@  What was wrong with the command line.
usage_error() {
    printf '%s: error: %s\n' "$PROG" "$*" >&2
    printf 'Usage: %s [options] <pattern>\n' "$PROG" >&2
    printf "Try '%s --help' for more information.\n" "$PROG" >&2
    exit "$EXIT_USAGE"
}

# ---- command line -----------------------------------------------------------

DRY_RUN=false
QUIET=false
SYNC_ALL=false
PATTERN=""
HAVE_PATTERN=false

# set_pattern - store the one positional argument.
#
# Args:
#   $1  The pattern as typed.
set_pattern() {
    if [[ "$HAVE_PATTERN" == true ]]; then
        usage_error "more than one pattern given (pass one at a time)"
    fi
    PATTERN="$1"
    HAVE_PATTERN=true
}

TMP_LIST=""   # NUL-separated match list, removed on exit

trap 'printf "\n" >&2; exit "$EXIT_INTERRUPTED"' INT
trap '[[ -z "$TMP_LIST" ]] || rm -f "$TMP_LIST"' EXIT

# Help is checked before anything else, so -h works even when the rest of
# the line is wrong. A word after '--' is a pattern, never a request.
for arg in "$@"; do
    case "$arg" in
        --) break ;;
        -h|--help) help_page; exit 0 ;;
        --*) ;;
        -*h*) help_page; exit 0 ;;   # -h inside a bundle such as -nh
    esac
done

# Options may come before or after the pattern (GNU style); short flags
# bundle (-sn); '--' ends the options so a pattern may start with '-'.
while [[ $# -gt 0 ]]; do
    case "$1" in
        --)
            shift
            while [[ $# -gt 0 ]]; do set_pattern "$1"; shift; done
            ;;
        -s|--sync)    SYNC_ALL=true; shift ;;
        -n|--dry-run) DRY_RUN=true; shift ;;
        -q|--quiet)   QUIET=true; shift ;;
        -V|--version) printf '%s %s\n' "$PROG" "$VERSION"; exit 0 ;;
        --*=*)        usage_error "option '${1%%=*}' does not take a value" ;;
        --*)          usage_error "unknown option '$1'" ;;
        -?*)
            # Split a bundle like -sn into -s -n and parse those.
            bundle="${1#-}"
            shift
            flags=()
            while [[ -n "$bundle" ]]; do
                flags+=("-${bundle:0:1}")
                bundle="${bundle:1}"
            done
            set -- "${flags[@]}" "$@"
            # A single unknown letter comes back here as itself; catch it.
            case "$1" in
                -s|-n|-q|-V) ;;
                *) usage_error "unknown option '$1'" ;;
            esac
            ;;
        *) set_pattern "$1"; shift ;;
    esac
done

if [[ "$HAVE_PATTERN" == false && "$SYNC_ALL" == false ]]; then
    usage_error "give a pattern to ignore, or use --sync"
fi
if [[ "$HAVE_PATTERN" == true ]]; then
    [[ -n "${PATTERN//[[:space:]]/}" ]] || usage_error "the pattern is empty"
    [[ "$PATTERN" != \#* ]] || \
        usage_error "a pattern starting with '#' is a comment in .gitignore (write '\\#' to match a leading #)"
    [[ "$PATTERN" != *$'\n'* ]] || usage_error "the pattern contains a newline"
fi

# ---- work -------------------------------------------------------------------

# Work from the top of the repository: .gitignore lives there, and git
# prints paths relative to where it runs.
if ! REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || [[ -z "$REPO_ROOT" ]]; then
    die "not inside a git work tree (cd into a repository first)"
fi
cd "$REPO_ROOT"

# pattern_listed - tell whether .gitignore already holds the pattern as a line.
#
# A line that differs only by a Windows CR ending counts as the same.
#
# Returns:
#   0 when the line is there, 1 when it is not (or there is no .gitignore).
pattern_listed() {
    [[ -f "$GITIGNORE" ]] || return 1
    grep -Fxq -e "$PATTERN" -e "$PATTERN"$'\r' -- "$GITIGNORE"
}

# add_pattern - append the pattern to .gitignore, once.
#
# Adds a newline first when the file does not end with one, so the pattern
# never joins the last line. With --dry-run nothing is written, not even an
# empty .gitignore.
add_pattern() {
    if pattern_listed; then
        say "'$PATTERN' is already in $GITIGNORE"
        return
    fi
    if [[ "$DRY_RUN" == true ]]; then
        say "Would add '$PATTERN' to $GITIGNORE"
        return
    fi
    if [[ -s "$GITIGNORE" && -n "$(tail -c 1 "$GITIGNORE")" ]]; then
        printf '\n' >> "$GITIGNORE"
    fi
    printf '%s\n' "$PATTERN" >> "$GITIGNORE"
    say "Added '$PATTERN' to $GITIGNORE"
}

# list_ignored - print, NUL-separated, every tracked path that would be ignored.
#
# git applies the rules exactly as it does for untracked files. The pattern
# (when given) is passed with -x, so a dry run sees it although it was not
# written; with --sync every .gitignore in the tree is read as well.
#
# Known limit: a pattern given on the command line outranks the .gitignore
# files, so a '!' rule in a subfolder's .gitignore does not protect a file
# from the new pattern here, although git would honour it later.
list_ignored() {
    local args=(ls-files -z --cached --ignored)
    if [[ "$HAVE_PATTERN" == true ]]; then
        args+=(--exclude="$PATTERN")
    fi
    if [[ "$SYNC_ALL" == true ]]; then
        args+=(--exclude-per-directory="$GITIGNORE")
    fi
    git "${args[@]}"
}

if [[ "$HAVE_PATTERN" == true ]]; then
    add_pattern
fi

# The list goes through a file, not a process substitution, so a failure of
# git ls-files stops the script instead of looking like "nothing matched".
TMP_LIST=$(mktemp "${TMPDIR:-/tmp}/git-ignore.XXXXXX") ||
    die "cannot create a temporary file in ${TMPDIR:-/tmp}"
list_ignored > "$TMP_LIST" || die "git ls-files failed (see git's message above)"
MATCHES=()
while IFS= read -r -d '' file; do
    MATCHES+=("$file")
done < "$TMP_LIST"

if [[ "$SYNC_ALL" == true ]]; then
    what="the .gitignore rules"
else
    what="'$PATTERN'"
fi

if [[ ${#MATCHES[@]} -eq 0 ]]; then
    say "No tracked files match $what"
    exit 0
fi

if [[ "$DRY_RUN" == true ]]; then
    say "Would untrack ${#MATCHES[@]} file(s) matching $what:"
else
    # Paths go in on stdin, which has no length limit, and literally, so a
    # tracked name such as 'foo[1].txt' is never read as a glob.
    if ! printf '%s\0' "${MATCHES[@]}" |
            git --literal-pathspecs rm --cached --quiet \
                --pathspec-from-file=- --pathspec-file-nul; then
        die "git rm --cached failed; nothing was untracked (see git's message above)"
    fi
    say "Untracked ${#MATCHES[@]} file(s) matching $what:"
fi
if [[ "$QUIET" == false ]]; then
    printf '%s\n' "${MATCHES[@]}"
fi
if [[ "$DRY_RUN" == false ]]; then
    say "Review with 'git status', then: git add $GITIGNORE && git commit"
fi

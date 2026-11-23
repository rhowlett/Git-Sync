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
#  File      : test_git-ignore.sh
#  Copyright : (c) 2026 brickChip and Richard Howlett. All rights reserved.
#  Author    : Richard Howlett <rhowlett@eenerd.com>
#  License   : PolyForm Noncommercial 1.0.0. Free for noncommercial use;
#              commercial use requires written permission from the owner.
#              Full terms in LICENSE at the project root.
#  Warranty  : None. Provided as is; the owner is not liable for any
#              damages arising from its use (No Liability, LICENSE).
#  BRICKCHIP-ENTRY-POINT: Run the git-ignore.sh tests
#  SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
################################################################################

# test_git-ignore.sh - end-to-end tests for git-ignore.sh.
#
# Each test builds a throw-away repository under a temporary folder, runs the
# script there and checks the index, the working tree, .gitignore, the output
# and the exit code. Nothing outside the temporary folder is touched, and the
# user's own git settings are not read (GIT_CONFIG_GLOBAL points at an empty
# file), so a personal excludes file cannot change the results.
#
# Usage:
#   bash tests/test_git-ignore.sh
#   TEST_BASH=/bin/bash bash tests/test_git-ignore.sh   # run the script
#                                                       # under another bash
# Exit status: 0 when every test passes, 1 when any fails.
#
# Written for bash 3.2 as well as 4 and 5, like the script under test.

# Every check passes the status of the test on the same line ("[[ ... ]];
# check ... $?"), which is exactly what SC2319 warns about. It is intended.
# A directive before the first command applies to the whole file.
# shellcheck disable=SC2319

set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT="$HERE/../git-ignore.sh"
TEST_BASH="${TEST_BASH:-bash}"   # the bash that runs the script under test

WORK=$(mktemp -d "${TMPDIR:-/tmp}/git-ignore-test.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

# Keep the user's ~/.gitconfig (and any global excludes) out of the tests.
: > "$WORK/gitconfig"
export GIT_CONFIG_GLOBAL="$WORK/gitconfig"
export GIT_CONFIG_NOSYSTEM=1
git config --global user.name "test"
git config --global user.email "test@example.com"
git config --global init.defaultBranch main

PASSED=0
FAILED=0
CURRENT=""   # name of the running test, for failure messages

# ---- helpers ----------------------------------------------------------------

# begin - start a test: print its name and make a fresh, empty repository.
#
# Args:
#   $1  Test name.
# Sets:
#   REPO  Path of the new repository; the shell is cd'd into it.
begin() {
    CURRENT="$1"
    REPO="$WORK/repo-$((PASSED + FAILED))"
    mkdir -p "$REPO"
    cd "$REPO" || exit 1
    git init -q
}

# commit_all - stage everything (ignored files too) and commit.
commit_all() {
    git add -A -f && git commit -q -m "fixture"
}

# run - run the script under test, capturing stdout, stderr and status.
#
# Args:
#   $@  Arguments for git-ignore.sh.
# Sets:
#   OUT, ERR  What it printed to stdout and stderr.
#   CODE      Its exit status.
run() {
    "$TEST_BASH" "$SCRIPT" "$@" > "$WORK/out" 2> "$WORK/err"
    CODE=$?
    OUT=$(cat "$WORK/out")
    ERR=$(cat "$WORK/err")
}

# check - record one assertion.
#
# Args:
#   $1  What is being checked, shown on failure.
#   $2  0 when the check passed (pass the status of a test command: $?).
check() {
    if [[ "$2" -eq 0 ]]; then
        PASSED=$((PASSED + 1))
    else
        FAILED=$((FAILED + 1))
        printf 'FAIL: %s: %s\n' "$CURRENT" "$1"
        printf '      exit %s\n      stdout: %s\n      stderr: %s\n' \
            "$CODE" "$OUT" "$ERR"
    fi
}

# tracked - succeed when the path is in the index.
#
# Args:
#   $1  Path relative to the repository top.
tracked() {
    [[ -n "$(git ls-files -- ":(literal)$1")" ]]
}

# ---- command line -----------------------------------------------------------

begin "version and help"
run --version
[[ $CODE -eq 0 && "$OUT" == "git-ignore.sh 0.1" ]]; check "--version prints '<prog> <version>'" $?
run -V
[[ $CODE -eq 0 && "$OUT" == "git-ignore.sh "* ]]; check "-V works" $?
run --help
[[ $CODE -eq 0 && "$OUT" == "git-ignore.sh - "* && -z "$ERR" ]]; check "--help goes to stdout, exit 0" $?
run --bogus --help
[[ $CODE -eq 0 && "$OUT" == *"Exit status:"* ]]; check "help wins over a bad option" $?
run -nh
[[ $CODE -eq 0 && "$OUT" == *"Usage:"* ]]; check "-h inside a bundle" $?
[[ $(printf '%s\n' "$OUT" | awk 'length > 78' | wc -l) -eq 0 ]]; check "help lines fit 78 columns" $?

begin "usage errors"
run
[[ $CODE -eq 2 && "$ERR" == "git-ignore.sh: error: give a pattern"* ]]; check "no arguments is exit 2" $?
[[ $(printf '%s\n' "$ERR" | wc -l) -eq 3 && "$ERR" == *"Try 'git-ignore.sh --help'"* ]]; check "usage error is three lines" $?
run --bogus x
[[ $CODE -eq 2 && "$ERR" == *"unknown option '--bogus'"* ]]; check "unknown long option" $?
run -sx
[[ $CODE -eq 2 && "$ERR" == *"unknown option '-x'"* ]]; check "unknown letter in a bundle" $?
run --sync=yes
[[ $CODE -eq 2 && "$ERR" == *"does not take a value"* ]]; check "--sync=value refused" $?
run a b
[[ $CODE -eq 2 && "$ERR" == *"more than one pattern"* ]]; check "two patterns refused" $?
run '#x'
[[ $CODE -eq 2 && "$ERR" == *"comment"* ]]; check "comment pattern refused" $?
run '   '
[[ $CODE -eq 2 && "$ERR" == *"empty"* ]]; check "blank pattern refused" $?
[[ ! -e .gitignore ]]; check "usage errors write nothing" $?

begin "outside a repository"
cd "$WORK" || exit 1
mkdir -p not-a-repo && cd not-a-repo || exit 1
GIT_CEILING_DIRECTORIES="$WORK" run '*.log'
[[ $CODE -eq 1 && "$ERR" == "git-ignore.sh: error: not inside a git work tree"* ]]; check "exit 1 outside a work tree" $?

# ---- adding a pattern -------------------------------------------------------

begin "folder pattern at any depth"
mkdir -p build sub/build docs src
touch build/a.o sub/build/b.o docs/readme.md src/build.c
commit_all
run 'build/'
[[ $CODE -eq 0 ]]; check "exit 0" $?
[[ "$(cat .gitignore)" == "build/" ]]; check ".gitignore holds the pattern" $?
! tracked build/a.o && ! tracked sub/build/b.o; check "both build folders untracked" $?
tracked src/build.c && tracked docs/readme.md; check "other files still tracked" $?
[[ -f build/a.o && -f sub/build/b.o ]]; check "files stay on disk" $?
[[ "$OUT" == "build/a.o"$'\n'"sub/build/b.o" ]]; check "stdout lists the untracked paths" $?
[[ -n "$(git diff --cached --name-only --diff-filter=D)" ]]; check "removal is staged" $?
[[ "$(git rev-list --count HEAD)" -eq 1 ]]; check "nothing committed" $?

begin "anchored and middle-slash patterns"
mkdir -p build sub/build docs/build lib/docs/build
touch build/a sub/build/b docs/build/c lib/docs/build/d
commit_all
run '/build'
! tracked build/a && tracked sub/build/b; check "'/build' only at the top" $?
run 'docs/build'
! tracked docs/build/c && tracked lib/docs/build/d; check "'docs/build' only that path" $?
run '**/build'
! tracked sub/build/b && ! tracked lib/docs/build/d; check "'**/build' at any depth" $?
[[ "$(cat .gitignore)" == "/build"$'\n'"docs/build"$'\n'"**/build" ]]; check "three lines, in order" $?

begin "dry run changes nothing"
touch a.log b.txt
commit_all
run --dry-run '*.log'
[[ $CODE -eq 0 && "$OUT" == "a.log" && "$ERR" == *"Would untrack 1"* ]]; check "reports what it would do" $?
[[ ! -e .gitignore ]]; check "no .gitignore created" $?
tracked a.log; check "index unchanged" $?
run '*.log' -n
[[ ! -e .gitignore ]] && tracked a.log; check "-n after the pattern works too" $?

begin "pattern already listed"
printf 'node_modules/\n*.log' > .gitignore   # no newline at the end
touch a.log keep.txt
commit_all
run '*.log'
[[ "$(grep -c '^\*\.log$' .gitignore)" -eq 1 ]]; check "not added twice" $?
[[ "$ERR" == *"already in .gitignore"* ]]; check "says it is already there" $?
! tracked a.log; check "still untracks matches" $?
run 'dist/'
[[ "$(cat .gitignore)" == "node_modules/"$'\n'"*.log"$'\n'"dist/" ]]; check "appended on a new line after a file with no final newline" $?

begin "run from a subfolder"
mkdir -p deep/er
touch deep/er/x.tmp top.tmp
commit_all
cd deep/er || exit 1
run '*.tmp'
cd "$REPO" || exit 1
[[ -f .gitignore && ! -e deep/er/.gitignore ]]; check "pattern goes to the top-level .gitignore" $?
! tracked top.tmp && ! tracked deep/er/x.tmp; check "matches across the whole repo" $?

begin "names that look like globs"
touch 'star*.tmp' 'starX.tmp' 'odd[1].txt' 'odd1.txt'
commit_all
run '/star\*.tmp'
! tracked 'star*.tmp' && tracked starX.tmp; check "only the literal 'star*.tmp' is untracked" $?
run '/odd\[1\].txt'
! tracked 'odd[1].txt' && tracked odd1.txt; check "only the literal 'odd[1].txt' is untracked" $?

begin "pattern starting with a dash"
touch -- -scratch keep
commit_all
run -- '-scratch'
[[ $CODE -eq 0 ]] && ! tracked -scratch && tracked keep; check "'--' lets a pattern start with '-'" $?

begin "quiet"
touch q.log
commit_all
run -q '*.log'
[[ $CODE -eq 0 && -z "$OUT" && -z "$ERR" ]] && ! tracked q.log; check "-q prints nothing on success" $?

begin "nothing matches"
touch a.c
commit_all
run '*.o'
[[ $CODE -eq 0 && -z "$OUT" && "$ERR" == *"No tracked files match"* ]]; check "exit 0 with a message" $?

begin "many files"
mkdir -p gen
i=0
while [[ $i -lt 3000 ]]; do : > "gen/file_with_a_fairly_long_name_$i.out"; i=$((i + 1)); done
commit_all
run -q 'gen/'
[[ $CODE -eq 0 && -z "$(git ls-files gen)" ]]; check "3000 files untracked in one call" $?

# ---- --sync -----------------------------------------------------------------

begin "sync uses every .gitignore"
mkdir -p sub
touch a.log sub/b.tmp sub/c.log keep.txt
commit_all
printf '*.log\n' > .gitignore
printf '*.tmp\n' > sub/.gitignore
run --sync
[[ $CODE -eq 0 ]]; check "exit 0" $?
! tracked a.log && ! tracked sub/c.log && ! tracked sub/b.tmp; check "root and nested rules applied" $?
tracked keep.txt; check "other files kept" $?
run --sync
[[ $CODE -eq 0 && "$ERR" == *"No tracked files match the .gitignore rules"* ]]; check "second run is clean" $?

begin "sync ignores personal excludes"
touch notes.secret local.txt
commit_all
printf '*.secret\n' > "$WORK/personal-excludes"
git config core.excludesFile "$WORK/personal-excludes"
printf 'local.txt\n' >> .git/info/exclude
run --sync
tracked notes.secret && tracked local.txt; check "core.excludesFile and info/exclude not used" $?

begin "sync with a pattern"
touch a.log b.bak
commit_all
printf '*.log\n' > .gitignore
run --sync --dry-run '*.bak'
[[ "$OUT" == "a.log"$'\n'"b.bak" ]] && tracked a.log && tracked b.bak; check "dry run lists both, changes nothing" $?
run -s '*.bak'
! tracked a.log && ! tracked b.bak; check "adds the pattern and syncs everything" $?
[[ "$(cat .gitignore)" == "*.log"$'\n'"*.bak" ]]; check ".gitignore gained the pattern" $?

# ---- result -----------------------------------------------------------------

printf '%d passed, %d failed\n' "$PASSED" "$FAILED"
[[ $FAILED -eq 0 ]]

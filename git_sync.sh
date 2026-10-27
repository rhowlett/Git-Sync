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
#  File      : git_sync.sh
#  Copyright : (c) 2026 brickChip and Richard Howlett. All rights reserved.
#  Author    : Richard Howlett <rhowlett@eenerd.com>
#  License   : PolyForm Noncommercial 1.0.0. Free for noncommercial use;
#              commercial use requires written permission from the owner.
#              Full terms in LICENSE at the project root.
#  BRICKCHIP-ENTRY-POINT: Check all repos in a folder; commit, pull and push interactively
#  SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
################################################################################

# git_sync.sh - check every git repo under a folder and sync it interactively.
#
# For each repo found (recursively; it does not look inside a repo once found):
#   1. fetch from the remote, so ahead/behind counts are current
#   2. if there are uncommitted changes, offer to stage everything and commit
#   3. if the branch is behind its upstream, show the incoming commits and
#      offer to pull (fast-forward, or rebase/merge when the branch diverged)
#   4. if the branch is ahead, show the outgoing commits and offer to push
#      (a branch with no upstream is offered "push and set upstream")
# A summary table is printed at the end.
#
# Every question defaults to No. Answer q at any prompt to stop and get the
# summary. A pull that hits conflicts is aborted automatically, so the repo is
# left exactly as it was.
#
# Works with the bash 3.2 that ships with macOS and with bash 4/5 on Linux.

usage() {
    cat <<'EOF'
Usage: git_sync.sh [options] [FOLDER]

Find every git repo under FOLDER (default: the current folder), fetch it,
and ask whether to commit, pull and push.

Options:
  -s, --status        Report only: show each repo's state, ask nothing,
                      change nothing (still fetches unless --no-fetch).
      --no-fetch      Don't contact remotes; use the last fetched state.
  -d, --max-depth N   How many folder levels below FOLDER to search
                      (default 8).
  -h, --help          Show this help.

At each prompt: y = yes, n or Enter = no, q = stop and show the summary.
Set NO_COLOR=1 to turn off colours.
EOF
}

# ----------------------------------------------------------------- options --
ROOT=""
DO_FETCH=1
STATUS_ONLY=0
MAX_DEPTH=8

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)      usage; exit 0 ;;
        -s|--status)    STATUS_ONLY=1 ;;
        --no-fetch)     DO_FETCH=0 ;;
        -d|--max-depth)
            shift
            case "${1:-}" in
                ''|*[!0-9]*) echo "error: --max-depth needs a number" >&2; exit 2 ;;
            esac
            MAX_DEPTH=$1 ;;
        --)             shift; break ;;
        -*)             echo "error: unknown option: $1" >&2; usage >&2; exit 2 ;;
        *)
            if [ -n "$ROOT" ]; then
                echo "error: only one folder can be given" >&2; exit 2
            fi
            ROOT=$1 ;;
    esac
    shift
done
if [ $# -gt 0 ]; then
    [ -n "$ROOT" ] && { echo "error: only one folder can be given" >&2; exit 2; }
    ROOT=$1
fi
[ -z "$ROOT" ] && ROOT=.

if ! command -v git >/dev/null 2>&1; then
    echo "error: git is not installed or not on PATH" >&2
    exit 1
fi
if ! abs_root=$(cd "$ROOT" 2>/dev/null && pwd); then
    echo "error: folder not found: $ROOT" >&2
    exit 1
fi
ROOT=$abs_root

# ------------------------------------------------------------------ colours --
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
    C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
    C_CYAN=$'\033[36m'
else
    C_RESET=""; C_BOLD=""; C_DIM=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_CYAN=""
fi

info() { printf '  %s\n' "$*"; }
ok()   { printf '  %s✓ %s%s\n' "$C_GREEN" "$*" "$C_RESET"; }
warn() { printf '  %s! %s%s\n' "$C_YELLOW" "$*" "$C_RESET"; }
fail() { printf '  %s✗ %s%s\n' "$C_RED" "$*" "$C_RESET"; }
indent() { sed 's/^/      /'; }

# ------------------------------------------------------------------ prompts --
QUIT=0

# ask "question" -> 0 for yes, 1 for no. Sets QUIT=1 on q or end of input.
ask() {
    local reply
    while :; do
        printf '  %s?%s %s %s[y/N/q]%s ' "$C_CYAN" "$C_RESET" "$1" "$C_DIM" "$C_RESET"
        if ! read -r reply; then echo; QUIT=1; return 1; fi
        case "$reply" in
            [yY]|[yY][eE][sS])      return 0 ;;
            ''|[nN]|[nN][oO])       return 1 ;;
            [qQ]|[qQ][uU][iI][tT])  QUIT=1; return 1 ;;
            *) info "Please answer y, n or q." ;;
        esac
    done
}

# choose_diverged -> sets CHOICE to r, m or s. Sets QUIT=1 on q or end of input.
CHOICE=s
choose_diverged() {
    local reply
    CHOICE=s
    while :; do
        printf '  %s?%s %s %s[r/m/S/q]%s ' "$C_CYAN" "$C_RESET" \
            "Rebase your commits on top, Merge, or Skip?" "$C_DIM" "$C_RESET"
        if ! read -r reply; then echo; QUIT=1; return; fi
        case "$reply" in
            [rR]|[rR][eE][bB][aA][sS][eE]) CHOICE=r; return ;;
            [mM]|[mM][eE][rR][gG][eE])     CHOICE=m; return ;;
            ''|[sS]|[sS][kK][iI][pP])       return ;;
            [qQ]|[qQ][uU][iI][tT])          QUIT=1; return ;;
            *) info "Please answer r, m, s or q." ;;
        esac
    done
}

# ------------------------------------------------------------- find repos --
shopt -s nullglob
REPOS=()

find_repos() {   # find_repos DIR DEPTH
    local dir=$1 depth=$2 sub
    if [ -e "$dir/.git" ]; then
        REPOS+=("$dir")
        return
    fi
    [ "$depth" -ge "$MAX_DEPTH" ] && return
    for sub in "$dir"/*/; do
        sub=${sub%/}
        [ -L "$sub" ] && continue              # don't follow symlinks (loops)
        case "${sub##*/}" in
            node_modules|.*) continue ;;
        esac
        find_repos "$sub" $((depth + 1))
    done
}

# ------------------------------------------------------------ repo state --
REPO=""                         # absolute path of the repo being processed
g() { git -C "$REPO" "$@"; }

# Globals set by read_state:
HAS_HEAD=0 BRANCH="" UPSTREAM="" UP_REMOTE="" UP_REF="" AHEAD=0 BEHIND=0
DIRTY=0 REMOTES=""

read_state() {
    local counts
    HAS_HEAD=0; BRANCH=""; UPSTREAM=""; UP_REMOTE=""; UP_REF=""
    AHEAD=0; BEHIND=0
    g rev-parse --verify -q HEAD >/dev/null 2>&1 && HAS_HEAD=1
    BRANCH=$(g symbolic-ref --quiet --short HEAD 2>/dev/null)
    REMOTES=$(g remote 2>/dev/null)
    DIRTY=$(g status --porcelain 2>/dev/null | wc -l | tr -d ' ')
    if [ -n "$BRANCH" ] && [ "$HAS_HEAD" -eq 1 ]; then
        UPSTREAM=$(g rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)
        if [ -n "$UPSTREAM" ]; then
            UP_REMOTE=$(g config "branch.$BRANCH.remote")
            UP_REF=$(g config "branch.$BRANCH.merge")
            counts=$(g rev-list --left-right --count 'HEAD...@{u}' 2>/dev/null)
            read -r AHEAD BEHIND <<<"$counts"
            AHEAD=${AHEAD:-0}; BEHIND=${BEHIND:-0}
        fi
    fi
}

# One-line description of the current state, for the summary.
describe_state() {
    local parts=""
    if [ -z "$BRANCH" ]; then
        parts="detached HEAD"
    elif [ "$HAS_HEAD" -eq 0 ]; then
        parts="no commits yet"
    elif [ -z "$REMOTES" ]; then
        parts="no remote"
    elif [ -z "$UPSTREAM" ]; then
        parts="no upstream"
    elif [ "$AHEAD" -eq 0 ] && [ "$BEHIND" -eq 0 ]; then
        parts="in sync"
    else
        [ "$AHEAD" -gt 0 ] && parts="$AHEAD to push"
        if [ "$BEHIND" -gt 0 ]; then
            [ -n "$parts" ] && parts="$parts, "
            parts="${parts}$BEHIND to pull"
        fi
    fi
    [ "$DIRTY" -gt 0 ] && parts="$parts, $DIRTY uncommitted"
    printf '%s' "$parts"
}

# Prints the operation in progress (merge, rebase...) or nothing.
in_progress() {
    local gd
    gd=$(g rev-parse --absolute-git-dir 2>/dev/null) || return
    if   [ -d "$gd/rebase-merge" ] || [ -d "$gd/rebase-apply" ]; then echo rebase
    elif [ -f "$gd/MERGE_HEAD" ];       then echo merge
    elif [ -f "$gd/CHERRY_PICK_HEAD" ]; then echo cherry-pick
    elif [ -f "$gd/REVERT_HEAD" ];      then echo revert
    fi
}

show_log() {    # show_log RANGE  - up to 10 one-line commits
    local range=$1 total
    total=$(g rev-list --count "$range" 2>/dev/null)
    g log --oneline --no-decorate -n 10 "$range" | indent
    [ "${total:-0}" -gt 10 ] && info "    ... and $((total - 10)) more"
}

plural() { [ "$1" -eq 1 ] && printf '%s %s' "$1" "$2" || printf '%s %ss' "$1" "$2"; }

# ----------------------------------------------------------- per repo --
SUM_NAME=()
SUM_DONE=()
SUM_STATE=()

process_repo() {
    local name=$1 done="" op out msg remote pushref n

    printf '\n%s%s%s\n' "$C_BOLD" "$name" "$C_RESET"

    if ! g rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        fail "Not a usable git work tree; skipped."
        SUM_DONE+=("-"); SUM_STATE+=("not a work tree"); return
    fi

    op=$(in_progress)
    if [ -n "$op" ]; then
        read_state
        warn "A $op is in progress. Finish or abort it by hand; skipped."
        SUM_DONE+=("-"); SUM_STATE+=("$op in progress"); return
    fi

    # ---- fetch
    REMOTES=$(g remote 2>/dev/null)
    if [ -n "$REMOTES" ] && [ "$DO_FETCH" -eq 1 ]; then
        if ! out=$(GIT_TERMINAL_PROMPT=0 g fetch --quiet --prune 2>&1); then
            warn "Fetch failed (offline or needs a login?). Using last known remote state."
            [ -n "$out" ] && printf '%s\n' "$out" | head -n 3 | indent
        fi
    fi

    read_state

    # ---- status line
    if [ -z "$BRANCH" ]; then
        info "Detached HEAD at $(g rev-parse --short HEAD 2>/dev/null)"
    elif [ -n "$UPSTREAM" ]; then
        info "Branch $C_BOLD$BRANCH$C_RESET -> $UPSTREAM   ahead $AHEAD, behind $BEHIND"
    elif [ -z "$REMOTES" ]; then
        info "Branch $C_BOLD$BRANCH$C_RESET   (no remote)"
    else
        info "Branch $C_BOLD$BRANCH$C_RESET   (no upstream)"
    fi
    if [ "$DIRTY" -gt 0 ]; then
        warn "$(plural "$DIRTY" "uncommitted change")"
    fi

    if [ "$STATUS_ONLY" -eq 1 ]; then
        SUM_DONE+=("-"); SUM_STATE+=("$(describe_state)"); return
    fi

    if [ -z "$BRANCH" ]; then
        info "Not on a branch, so nothing to pull or push. Check out a branch first."
        SUM_DONE+=("-"); SUM_STATE+=("$(describe_state)"); return
    fi

    # ---- 1. commit
    if [ "$DIRTY" -gt 0 ]; then
        g status --short | head -n 20 | indent
        [ "$DIRTY" -gt 20 ] && info "    ... and $((DIRTY - 20)) more"
        if ask "Stage ALL of these (including new files) and commit?"; then
            printf '  Commit message (empty to cancel): '
            if ! read -r msg; then
                echo; QUIT=1; msg=""
            fi
            if [ -n "$msg" ]; then
                if out=$(g add -A 2>&1 && g commit -q -m "$msg" 2>&1); then
                    ok "Committed."
                    done="committed"
                else
                    fail "Commit failed:"; printf '%s\n' "$out" | indent
                fi
            else
                info "Commit cancelled."
            fi
            read_state
        fi
        [ "$QUIT" -eq 1 ] && { SUM_DONE+=("${done:--}"); SUM_STATE+=("$(describe_state)"); return; }
    fi

    # ---- 2. pull
    if [ -n "$UPSTREAM" ] && [ "$BEHIND" -gt 0 ]; then
        info "Incoming from $UPSTREAM:"
        show_log 'HEAD..@{u}'
        if [ "$AHEAD" -eq 0 ]; then
            if ask "Pull $(plural "$BEHIND" commit) (fast-forward)?"; then
                if out=$(g merge --ff-only --quiet '@{u}' 2>&1); then
                    ok "Pulled $(plural "$BEHIND" commit)."
                    done="${done:+$done, }pulled $BEHIND"
                else
                    fail "Pull refused (it would overwrite uncommitted changes?). Nothing was changed:"
                    printf '%s\n' "$out" | head -n 10 | indent
                fi
            fi
        elif [ "$DIRTY" -gt 0 ]; then
            warn "Branch has diverged ($AHEAD local, $BEHIND remote commits) and has"
            warn "uncommitted changes. Commit or stash them, then run again to pull."
        else
            warn "Branch has diverged: $AHEAD local and $BEHIND remote commits."
            choose_diverged
            case "$CHOICE" in
                r)
                    if out=$(g rebase --quiet '@{u}' 2>&1); then
                        ok "Rebased $(plural "$AHEAD" commit) onto $UPSTREAM."
                        done="${done:+$done, }rebased"
                    else
                        g rebase --abort >/dev/null 2>&1
                        fail "Rebase hit conflicts and was aborted; the repo is unchanged."
                        info "Resolve by hand:  cd \"$REPO\" && git pull --rebase"
                    fi ;;
                m)
                    if out=$(g merge --no-edit --quiet '@{u}' 2>&1); then
                        ok "Merged $UPSTREAM."
                        done="${done:+$done, }merged"
                    else
                        g merge --abort >/dev/null 2>&1
                        fail "Merge hit conflicts and was aborted; the repo is unchanged."
                        info "Resolve by hand:  cd \"$REPO\" && git pull"
                    fi ;;
            esac
        fi
        read_state
        [ "$QUIT" -eq 1 ] && { SUM_DONE+=("${done:--}"); SUM_STATE+=("$(describe_state)"); return; }
    fi

    # ---- 3. push
    if [ -n "$UPSTREAM" ]; then
        if [ "$AHEAD" -gt 0 ] && [ "$BEHIND" -eq 0 ]; then
            info "Outgoing to $UPSTREAM:"
            show_log '@{u}..HEAD'
            if ask "Push $(plural "$AHEAD" commit) to $UPSTREAM?"; then
                if out=$(g push --quiet "$UP_REMOTE" "HEAD:$UP_REF" 2>&1); then
                    ok "Pushed $(plural "$AHEAD" commit)."
                    done="${done:+$done, }pushed $AHEAD"
                else
                    fail "Push failed:"; printf '%s\n' "$out" | head -n 10 | indent
                fi
            fi
        elif [ "$AHEAD" -gt 0 ]; then
            info "$(plural "$AHEAD" "local commit") can't be pushed until the $BEHIND remote ones are pulled."
        fi
    elif [ -z "$REMOTES" ]; then
        info "No remote configured, so nothing to pull or push."
    elif [ "$HAS_HEAD" -eq 0 ]; then
        info "No commits yet, so nothing to push."
    else
        # Branch has no upstream: offer to publish it.
        if printf '%s\n' "$REMOTES" | grep -qx origin; then
            remote=origin
        else
            remote=$(printf '%s\n' "$REMOTES" | head -n 1)
        fi
        pushref="refs/remotes/$remote/$BRANCH"
        if g rev-parse --verify -q "$pushref" >/dev/null; then
            n=$(g rev-list --count "$pushref..HEAD")
            info "$remote/$BRANCH exists but this branch doesn't track it."
            if [ "$n" -gt 0 ] && ask "Push $(plural "$n" commit) to $remote/$BRANCH and set it as upstream?"; then
                if out=$(g push --quiet -u "$remote" "$BRANCH" 2>&1); then
                    ok "Pushed and now tracking $remote/$BRANCH."
                    done="${done:+$done, }pushed $n"
                else
                    fail "Push failed:"; printf '%s\n' "$out" | head -n 10 | indent
                fi
            elif [ "$n" -eq 0 ]; then
                info "Nothing new to push. To track it: git branch -u $remote/$BRANCH"
            fi
        else
            n=$(g rev-list --count HEAD)
            info "Branch '$BRANCH' is not on $remote yet."
            if ask "Push it to $remote/$BRANCH and set upstream?"; then
                if out=$(g push --quiet -u "$remote" "$BRANCH" 2>&1); then
                    ok "Published $BRANCH to $remote."
                    done="${done:+$done, }published"
                else
                    fail "Push failed:"; printf '%s\n' "$out" | head -n 10 | indent
                fi
            fi
        fi
    fi

    read_state
    [ "$AHEAD" -eq 0 ] && [ "$BEHIND" -eq 0 ] && [ "$DIRTY" -eq 0 ] && \
        [ -n "$UPSTREAM" ] && [ -z "$done" ] && ok "Up to date."
    SUM_DONE+=("${done:--}")
    SUM_STATE+=("$(describe_state)")
}

# -------------------------------------------------------------- summary --
print_summary() {
    local i w=4 n=${#SUM_STATE[@]}
    [ "$n" -eq 0 ] && return
    for ((i = 0; i < n; i++)); do
        [ "${#SUM_NAME[i]}" -gt "$w" ] && w=${#SUM_NAME[i]}
    done
    printf '\n%sSummary%s\n' "$C_BOLD" "$C_RESET"
    if [ "$STATUS_ONLY" -eq 1 ]; then
        printf "  %-${w}s  %s\n" "Repo" "State"
        for ((i = 0; i < n; i++)); do
            printf "  %-${w}s  %s\n" "${SUM_NAME[i]}" "${SUM_STATE[i]}"
        done
    else
        printf "  %-${w}s  %-28s  %s\n" "Repo" "Done" "Now"
        for ((i = 0; i < n; i++)); do
            printf "  %-${w}s  %-28s  %s\n" "${SUM_NAME[i]}" "${SUM_DONE[i]}" "${SUM_STATE[i]}"
        done
    fi
    if [ "$QUIT" -eq 1 ] && [ "$n" -lt ${#REPOS[@]} ]; then
        printf '  %s(stopped early: %d of %d repos not checked)%s\n' \
            "$C_DIM" $(( ${#REPOS[@]} - n )) ${#REPOS[@]} "$C_RESET"
    fi
}

on_interrupt() {
    echo
    warn "Interrupted."
    # Drop a half-recorded entry so the arrays line up.
    while [ ${#SUM_NAME[@]} -gt ${#SUM_STATE[@]} ]; do
        unset 'SUM_NAME[${#SUM_NAME[@]}-1]'
    done
    print_summary
    exit 130
}
trap on_interrupt INT

# ----------------------------------------------------------------- main --
find_repos "$ROOT" 0

if [ ${#REPOS[@]} -eq 0 ]; then
    echo "No git repos found under $ROOT (searched $MAX_DEPTH levels deep)."
    exit 0
fi

mode="interactive"
[ "$STATUS_ONLY" -eq 1 ] && mode="status only"
[ "$DO_FETCH" -eq 0 ] && mode="$mode, no fetch"
printf '%sFound %d git repo(s) under %s%s  (%s)\n' \
    "$C_BOLD" ${#REPOS[@]} "$ROOT" "$C_RESET" "$mode"

for REPO in "${REPOS[@]}"; do
    if [ "$REPO" = "$ROOT" ]; then
        name=${ROOT##*/}
    else
        name=${REPO#"$ROOT"/}
    fi
    SUM_NAME+=("$name")
    process_repo "$name"
    [ "$QUIT" -eq 1 ] && break
done

print_summary

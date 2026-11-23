<!-- v0.1 -->
# Solo workflow: CI, pull requests and merging

How changes get into `main` in this repository when one person does all
the work. Every change goes through a short pull request (PR), CI tests it
on Linux and macOS, and GitHub only lets it into `main` when CI is green.
You are the author, the reviewer and the person who merges.

```
main ──●────────────────────────●───────────  (always passes CI)
        \                      /
         ●──●──●  feature branch      push ─▶ PR ─▶ CI ─▶ squash merge
```

**Why bother when you work alone?**

- **`main` always works.** Nothing lands until shellcheck and the tests
  pass on both operating systems, including the bash 3.2 that macOS ships.
- **One commit per change.** Squash merging turns a messy branch into one
  commit on `main` with a title and a description of why.
- **Easy undo.** Every change is a PR with a **Revert** button.
- **Mistakes are blocked.** An accidental `git push` to `main` from another
  machine (or from `git_sync.sh`) is refused instead of skipping CI.

Part 1 is done once. Part 2 is the loop for every change. Part 3 covers
what to do when something goes sideways. Part 4 is a one-screen summary.

---

## Part 1: One-time setup (about 10 minutes)

Do these steps in order. The ruleset (step 4) needs CI to have run once,
so it comes after the first PR is open.

### 1. Check that Actions is allowed

The CI workflow is already in the repository at
`.github/workflows/ci.yml`. It runs on every PR, every push to `main`, and
on demand.

1. On the repository page, open **Settings ▸ Actions ▸ General**.
2. Under **Actions permissions**, make sure **Allow all actions and
   reusable workflows** is selected (the default). Click **Save** if you
   changed it.

Public repositories get GitHub-hosted runners for free, macOS included.

What CI runs, as three jobs plus a summary:

| Job | What it does |
|---|---|
| `shellcheck` | `bash -n` and `shellcheck` on every `*.sh`; `git_sync.sh --help` smoke test |
| `tests (ubuntu-latest)` | `tests/test_git-ignore.sh` under Linux bash 5 |
| `tests (macos-latest)` | the same tests under macOS `/bin/bash` 3.2 |
| `CI passed` | fails unless all of the above succeeded; this is the one check `main` requires |

### 2. Set the merge options

Open **Settings ▸ General** and scroll to **Pull Requests**:

| Setting | Set to | Why |
|---|---|---|
| Allow merge commits | off | keeps `main` a straight line |
| Allow squash merging | **on**, default message **Pull request title and description** | one commit per PR, with the PR text as its message |
| Allow rebase merging | off | one way to merge, no choices to get wrong |
| Always suggest updating pull request branches | **on** | shows an **Update branch** button when `main` has moved |
| Allow auto-merge | **on** | lets a PR merge itself the moment CI goes green |
| Automatically delete head branches | **on** | the branch on GitHub is deleted after merging |

### 3. Install the GitHub CLI (optional, recommended)

Everything in this document can be done in the browser, but `gh` saves
many clicks. Each step in Part 2 shows both.

```bash
brew install gh          # macOS (Homebrew)
sudo apt install gh      # Ubuntu 22.04 or newer
gh auth login            # once: choose GitHub.com, HTTPS, log in with a browser
```

### 4. Protect `main` with a ruleset

Open the first PR before this step (for example the one that added this
document) and let its CI finish, so GitHub has seen the `CI passed` check.

1. Open **Settings**, then in the left sidebar **Rules ▸ Rulesets**.
2. Click **New ruleset ▸ New branch ruleset**.
3. **Ruleset name:** `protect main`.
4. **Enforcement status:** **Active**.
5. **Bypass list:** leave it **empty**. Then the rules apply to you too,
   which is the point: nothing reaches `main` without CI. (Part 3 explains
   how to get past them in an emergency.)
6. **Target branches:** click **Add target ▸ Include default branch**.
7. Under **Branch protections**, tick:

   | Rule | Setting |
   |---|---|
   | Restrict deletions | on (default) |
   | Require linear history | on |
   | Require a pull request before merging | on, **Required approvals: 0**, Allowed merge methods: **Squash** only |
   | Require status checks to pass | on; tick **Require branches to be up to date before merging**; add the check **`CI passed`** |
   | Block force pushes | on (default) |

   Required approvals must be **0**: GitHub never lets the author of a PR
   approve it, so with 1 or more you could never merge your own work. Your
   review is the read-through in Part 2, step 6.

   To add the check, type `CI passed` in the status-check box and pick it
   from the list (or click the **+** to add the name as typed). Leave the
   source as "any source".
8. Click **Create**.

### 5. Prove it works

```bash
git switch main && git pull --ff-only
git commit --allow-empty -m "test: should be refused"
git push origin main
```

GitHub should refuse with `GH013: Repository rule violations found` and
list "Changes must be made through a pull request". Then throw the test
commit away:

```bash
git reset --hard origin/main
```

---

## Part 2: Every change

### 1. Start from an up-to-date `main`

```bash
git switch main
git pull --ff-only
```

`--ff-only` never makes a merge commit. If it refuses, something is on your
local `main` that isn't on GitHub; see Part 3, "Local main has extra
commits".

### 2. Make a branch

```bash
git switch -c fix/sync-dry-run
```

Name branches `<kind>/<what>`, all lower case with dashes:

| Kind | For |
|---|---|
| `add/` | a new script or feature (`add/git-ignore`) |
| `fix/` | a bug fix |
| `docs/` | documentation only |
| `ci/` | the workflow or tests only |

### 3. Do the work, the project-conventions way

- Bump the version tag (line 2, after the shebang) of every file you
  change, and the program's `VERSION` when behavior changes.
- Add an entry to `documents/change_log.md`: files, new versions, what
  changed, why.
- Update the README or other docs that describe the changed behavior.
- Run the same checks CI runs, before you push:

  ```bash
  shellcheck -- *.sh tests/*.sh
  bash tests/test_git-ignore.sh
  ```

  Install shellcheck once with `brew install shellcheck` or
  `sudo apt install shellcheck`.

Commit as often as you like; the commits are squashed into one at the end.

```bash
git add -A
git commit -m "Fix: --dry-run no longer creates .gitignore"
```

### 4. Push the branch

```bash
git push -u origin HEAD
```

`-u` links your local branch to the one on GitHub, so later pushes are
just `git push`.

### 5. Open the pull request

**Browser:** the repository page shows a yellow **Compare & pull request**
banner for the branch you just pushed. Click it. Base is `main`, compare is
your branch.

**CLI:**

```bash
gh pr create --fill          # title and body from your commits; edit after
gh pr create --web           # or open the form in the browser
```

The form is pre-filled from `.github/pull_request_template.md`. Fill it in:

- **Title** becomes the commit subject on `main`, so write it the way you
  want to read it in `git log` a year from now: `Add git-ignore.sh`,
  `Fix: --sync used personal excludes`.
- **Body** becomes the commit description: what changed, why, how you
  checked it.

**Tip, auto-merge:** right after opening, turn on auto-merge and walk away.
The PR merges itself as soon as CI is green, and stays open if CI fails.

```bash
gh pr merge --auto --squash
```

(Browser: **Enable auto-merge ▸ Squash and merge ▸ Confirm**.)

### 6. Review your own PR

Open the **Files changed** tab and read the diff as if someone else wrote
it. This is where you catch debugging leftovers and files you didn't mean
to commit. Check:

- [ ] Only the files you meant to change, nothing extra (`.DS_Store`,
      scratch files, secrets).
- [ ] Version tags bumped in every changed source file.
- [ ] Change-log entry added.
- [ ] README and docs match the new behavior.
- [ ] The PR description says why.

Something to fix? Fix it on the branch, commit, `git push`. The PR updates
and CI runs again.

### 7. Wait for CI

The **Checks** section at the bottom of the PR shows each job. Or:

```bash
gh pr checks --watch
```

- **All green:** go to step 8 (or auto-merge already did it).
- **Red:** click **Details** next to the failed job to see the log. Fix on
  the branch, commit, `git push`; CI reruns on its own.
- **"This branch is out-of-date with the base branch":** `main` moved since
  you branched. Click **Update branch** (or see Part 3). CI runs again.

### 8. Merge

**Browser:** **Squash and merge**, check the commit title and description it
offers, **Confirm squash and merge**.

**CLI:**

```bash
gh pr merge --squash --delete-branch
```

GitHub deletes the branch on its side (step 2 of Part 1).

### 9. Clean up locally

```bash
git switch main
git pull --ff-only
git fetch --prune                  # forget branches deleted on GitHub
git branch -D fix/sync-dry-run     # delete your local copy
```

`-D` (capital) is needed after a squash merge: the squashed commit on
`main` has a different hash from your branch's commits, so git can't tell
the branch was merged and `-d` refuses. Check the PR says **Merged** before
you delete. (`gh pr merge --delete-branch` does this local cleanup for you
when you run it from the repository.)

---

## Part 3: When things go sideways

### `main` moved while your PR was open

Another PR merged, or you pushed from another machine. With "Require
branches to be up to date" on, GitHub won't merge until you catch up.

- **Easiest:** click **Update branch** on the PR (choose **Update with
  rebase** if offered).
- **From the terminal:**

  ```bash
  git fetch origin
  git rebase origin/main
  git push --force-with-lease
  ```

  Force-pushing your own feature branch is fine; only `main` blocks it.
  `--force-with-lease` refuses if the branch on GitHub has commits you
  haven't seen, so you can't overwrite work from another machine by
  accident.

If the rebase stops on a conflict, fix the marked files, `git add` them and
`git rebase --continue`, or `git rebase --abort` to go back.

### Local `main` has extra commits

You committed on `main` by mistake, and `git pull --ff-only` or
`git push` refuses. Move the commits to a branch and reset `main`:

```bash
git switch -c fix/whatever       # the branch now holds your commits
git switch main
git reset --hard origin/main     # main back to what GitHub has
git switch fix/whatever          # carry on from Part 2, step 4
```

### Undo a merged change

Open the merged PR and click **Revert**. GitHub makes a new branch and PR
that undoes it; it goes through CI and merges like any other PR.

### CI failed for a reason that isn't your code

A runner hiccup or a network timeout. On the PR, open **Checks**, then
**Re-run jobs ▸ Re-run failed jobs**. Or `gh run rerun --failed`.

### `git_sync.sh` says the push to `main` was rejected

That's the ruleset doing its job. In this repository, commit on a branch
and use Part 2. `git_sync.sh` still pulls `main` fine; only pushes to it are
refused. Your other repositories aren't affected.

### Emergency: you must change `main` without CI

For example, the workflow file itself is broken so no PR can pass.

1. **Settings ▸ Rules ▸ Rulesets ▸ protect main**.
2. Set **Enforcement status** to **Disabled**, **Save changes**.
3. Make the fix (a PR merged despite red checks, or a direct push).
4. Set it back to **Active** straight away.

Prefer fixing the workflow in a PR first: a PR can change the workflow and
its own CI run uses the new version.

### Changing the CI jobs

Edit `.github/workflows/ci.yml` in a PR like any other change. The ruleset
only requires `CI passed`, so new or renamed jobs need no ruleset change as
long as they are listed under `needs:` of the `ci-passed` job.

---

## Part 4: Quick reference

```bash
# start
git switch main && git pull --ff-only
git switch -c fix/thing

# work, then check like CI does
shellcheck -- *.sh tests/*.sh && bash tests/test_git-ignore.sh
git add -A && git commit -m "Fix: thing"

# publish and let it merge itself when green
git push -u origin HEAD
gh pr create --fill
gh pr merge --auto --squash

# after it merges
git switch main && git pull --ff-only && git fetch --prune
git branch -D fix/thing
```

| Situation | Do |
|---|---|
| CI red | read **Details**, fix, `git push` |
| Branch out of date | **Update branch**, or `git rebase origin/main && git push --force-with-lease` |
| Committed on `main` by mistake | `git switch -c fix/x`, `git switch main`, `git reset --hard origin/main` |
| Undo a merged PR | **Revert** button on the PR |
| Flaky CI | `gh run rerun --failed` |

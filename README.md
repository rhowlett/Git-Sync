<!-- v0.1 -->
# git_sync

Keep a folder full of git repos in sync from one command.

`git_sync.sh` looks through the folder you keep your repos in (for example
`~/GitHub`) and finds every git repo in it. For each one it fetches from the
remote, shows what's waiting, and asks before it does anything:

- **Uncommitted changes:** lists them and offers to stage everything, commit with
  a message you type, and push.
- **Behind the remote:** shows the incoming commits and offers to pull.
- **Ahead of the remote:** shows the outgoing commits and offers to push.
- **Diverged** (new commits on both sides): offers rebase, merge or skip. If
  either one hits a conflict it is cancelled automatically, so the repo is left
  exactly as it was.
- **Branch not on the remote yet:** offers to push it and set its upstream.

At the end, a summary table shows what was done in each repo and where each
one stands now.

It runs on **macOS** and **Linux (Ubuntu 22.04 or newer)** and needs only `bash`
and `git`. The bash that comes with macOS works; nothing else needs to be
installed.

## Install

Download the ZIP and put the script in the **base folder that holds your repo
folders**. You don't need to clone this repo.

The examples use `~/GitHub`. Use your own folder if it's somewhere else.

```
~/GitHub/
├── git_sync.sh      <- the script goes here
├── project-one/     <- your repos
├── project-two/
└── some-org/
    └── project-three/
```

### macOS (Finder)

1. On this repo's GitHub page, click **Code ▸ Download ZIP**.
2. Find the download in your **Downloads** folder. Safari usually unzips it for
   you into a folder named `git_sync-main`. If you have `git_sync-main.zip`
   instead, double-click it to unzip it.
3. Drag **`git_sync.sh`** out of `git_sync-main` into your `GitHub` folder.
4. Delete the `git_sync-main` folder and the ZIP if it's still there. You only
   need the script.
5. Open **Terminal** and make the script runnable (needed only once):

   ```bash
   cd ~/GitHub
   chmod +x git_sync.sh
   ```

### macOS or Linux (Terminal)

Replace `OWNER` with the GitHub account this repo lives under.

```bash
cd ~/GitHub
curl -L -o git_sync.zip https://github.com/OWNER/git_sync/archive/refs/heads/main.zip
unzip -q git_sync.zip
mv git_sync-main/git_sync.sh .
rm -rf git_sync-main git_sync.zip
chmod +x git_sync.sh
```

On Ubuntu, if `unzip` is missing, install it with `sudo apt install unzip`.

### Check it works

```bash
cd ~/GitHub
./git_sync.sh --status
```

This lists every repo with its state ("in sync", "2 to pull", "1 to push",
"3 uncommitted"...). It asks nothing and doesn't touch your files or branches;
it only runs `git fetch` in each repo.

## Run it

```bash
cd ~/GitHub
./git_sync.sh
```

Answer each question with:

| Key | Meaning |
|---|---|
| `y` | yes |
| `n` or Enter | no (the default for every question) |
| `q` | stop now and show the summary |

For a diverged branch the choices are `r` (rebase), `m` (merge) or `s`/Enter
(skip).

### Example

```
$ ./git_sync.sh
Found 3 git repo(s) under /Users/you/GitHub  (interactive)

JSONScope
  Branch main -> origin/main   ahead 0, behind 2
  Incoming from origin/main:
      33bd59d Add Sort Keys hotkey
      cc35d3e Fix tree paste into empty array
  ? Pull 2 commits (fast-forward)? [y/N/q] y
  ✓ Pulled 2 commits.

PowerBrick
  Branch main -> origin/main   ahead 0, behind 0
  ! 2 uncommitted changes
       M a
      ?? notes.md
  ? Stage ALL of these (including new files) and commit? [y/N/q] y
  Commit message (empty to cancel): Update charger notes
  ✓ Committed.
  Outgoing to origin/main:
      84d3238 Update charger notes
  ? Push 1 commit to origin/main? [y/N/q] y
  ✓ Pushed 1 commit.

website
  Branch main -> origin/main   ahead 1, behind 0
  Outgoing to origin/main:
      37874b1 Update contact page
  ? Push 1 commit to origin/main? [y/N/q] y
  ✓ Pushed 1 commit.

Summary
  Repo        Done                          Now
  JSONScope   pulled 2                      in sync
  PowerBrick  committed, pushed 1           in sync
  website     pushed 1                      in sync
```

### Options

```
./git_sync.sh [options] [FOLDER]
```

| Option | What it does |
|---|---|
| `FOLDER` | Folder to search. Defaults to the current folder. |
| `-s`, `--status` | Report only. Asks nothing and changes nothing. |
| `--no-fetch` | Don't contact the remotes (for example when offline). Uses the last fetched state. |
| `-d N`, `--max-depth N` | How many folder levels to search (default 8). |
| `-h`, `--help` | Show help. |

Colours are turned off automatically when the output isn't a terminal, or when
`NO_COLOR=1` is set.

### Running it from anywhere (optional)

To type `git_sync` from any folder, link the script into a folder on your
`PATH` and give it the folder to check:

```bash
mkdir -p ~/.local/bin
ln -s ~/GitHub/git_sync.sh ~/.local/bin/git_sync
git_sync ~/GitHub
```

Ubuntu adds `~/.local/bin` to `PATH` automatically once the folder exists (log
out and back in after creating it). On macOS, add
`export PATH="$HOME/.local/bin:$PATH"` to `~/.zshrc`.

## How it decides what to do

- **Finding repos:** any folder containing `.git` counts as a repo. Once it
  finds a repo it doesn't look inside it, so nested repos and submodules aren't
  listed separately. It skips hidden folders, `node_modules` and symbolic links.
- **Current branch only:** each repo's checked-out branch is synced. Other
  branches aren't touched.
- **Pull:** a fast-forward is offered whenever the branch is only behind.
  If incoming changes would overwrite files you've edited but not committed,
  git refuses and nothing changes. Commit those files first (the script offers
  to), then run it again.
- **Commit:** "stage all" means `git add -A`: modified, deleted **and new
  untracked files**, except anything your `.gitignore` excludes. Read the list
  before you answer `y`.
- **Push:** offered only when the branch is ahead and not behind, so it won't
  be rejected for being out of date (unless someone pushes in between). The
  commits are pushed to the branch's own upstream.
- **Skipped repos:** repos on a detached HEAD, or with a merge, rebase,
  cherry-pick or revert half-finished, are reported and left alone. Repos with
  no remote are reported only.
- **Logins:** fetching never waits for a password. If a remote needs one, the
  fetch is reported as failed and the script carries on. Pushing does ask, the
  same way `git push` normally does. SSH keys and the macOS Keychain work as
  usual.

## Updating

Download the ZIP again and replace `git_sync.sh` in your base folder using the
same steps as installing. The version is on line 2 of the script:

```bash
sed -n 2p ~/GitHub/git_sync.sh
```

## Uninstall

Delete `git_sync.sh` from your base folder, and the `~/.local/bin/git_sync`
link if you made one. The script stores no settings or other files.

## Troubleshooting

| Problem | Fix |
|---|---|
| `permission denied: ./git_sync.sh` | Run `chmod +x git_sync.sh` once. |
| `No git repos found` | You're not in the base folder. `cd` there, or pass it: `./git_sync.sh ~/GitHub`. |
| `Fetch failed (offline or needs a login?)` | Check your network, or run `git fetch` in that repo by hand to sign in once. |
| A repo deeper than 8 levels isn't found | Use `-d 12` (or more). |
| `bad interpreter` or `$'\r': command not found` | The file picked up Windows line endings. Fix with `sed -i '' 's/\r$//' git_sync.sh` on macOS or `sed -i 's/\r$//' git_sync.sh` on Linux. |

<!-- BRICKCHIP-LICENSE:BEGIN -->
## Copyright & License

```text
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
```

**Project:** git_sync  
**Copyright:** © 2026 brickChip and Richard Howlett. All rights reserved.  
**Owner / Author:** Richard Howlett · rhowlett@eenerd.com

All source code, documentation and other files in this project are licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE) (SPDX: `PolyForm-Noncommercial-1.0.0`).

- **Free** to use, copy, modify and share for any noncommercial purpose: personal, hobby, research, education.
- **Commercial use** requires a separate written license. This includes selling it, building it into a product or paid service, or using it in for-profit work. Contact rhowlett@eenerd.com.

### Entry points

Every file that starts something in this project carries a `BRICKCHIP-ENTRY-POINT` line, so `grep -rn BRICKCHIP-ENTRY-POINT .` lists them all:

| File | Runs |
|---|---|
| `git_sync.sh` | Check all repos in a folder; commit, pull and push interactively |

<!-- BRICKCHIP-LICENSE:END -->

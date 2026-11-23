## What

<!-- One or two sentences. The PR title becomes the commit subject on main. -->

## Why

<!-- The reason or the problem it fixes. This text becomes the commit body. -->

## How I checked it

<!-- Commands run and what you saw, e.g. "bash tests/test_git-ignore.sh: 52 passed". -->

## Checklist

- [ ] Version tag bumped in every changed source file
- [ ] `documents/change_log.md` entry added
- [ ] README / docs match the new behavior
- [ ] `shellcheck -- *.sh tests/*.sh` and `bash tests/test_git-ignore.sh` pass locally
- [ ] Read the **Files changed** tab: nothing unintended

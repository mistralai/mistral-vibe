---
name: worktree
description: Create, reuse, and clean up git worktrees under $VIBE_HOME/worktrees. Use when the user wants to work in an isolated git worktree, create a worktree for a task, or list/remove existing worktrees.
user-invocable: true
---

# Worktree Management

Git worktrees live under `$VIBE_HOME/worktrees/` (default `~/.vibe/worktrees/`),
each in a per-repository bucket named `<repo_root.name>-<sha256(common_git_dir)[:12]>`
so worktrees from different repos never collide.

```
$VIBE_HOME/worktrees/
  .claims/<bucket>/<name>/          # claim records and holder markers
    record.json                     # metadata: branch, base_commit, branch_created, claimed_at
    holders/                        # one empty file per live session
  <bucket>/<name>/                  # the actual git worktree checkout
```

## Creating a worktree

1. Compute the bucket from the repo root and common git dir.
2. Validate the name (single portable path segment; no `<>:"/\|?*`, no
   trailing space or dot, no Windows reserved names like `CON`/`PRN`/`NUL`/
   `COM1`-`COM9`/`LPT1`-`LPT9`, not `.` or `..`).
3. Reserve the directory with `mkdir` (atomic claim — fails if taken).
4. For a new branch, start from the remote's default branch (fetch first,
   best-effort). For an existing branch, check it out into the worktree.
5. `git worktree add -b <name> <path> <start_point>` (or without `-b` for reuse).
6. Record the worktree's own HEAD as `base_commit` — this is the baseline for
   cleanup, not the invoking checkout's HEAD.
7. Write `record.json` under `.claims/<bucket>/<name>/`.

When no name is given, derive one by slugifying the prompt: NFKD-normalize,
lowercase, replace non-`[a-z0-9]+` with `-`, take the first 6 words, trim to
40 chars, drop trailing stop words. Branch gets a `vibe/` prefix. Append
`-2`, `-3`, etc. on collision (up to 100). Fall back to a random slug if empty.

Reuse an existing worktree only after validating: no symlinks in the path,
`.git` is a file (not a directory), same common git dir, matching branch.

## Holding

A session using a worktree must register as a holder to prevent the
background cleanup from removing it. Holder files are locked for the lifetime
of the session; an unlocked marker left by a crashed process is stale and may
be removed. A temporary holder protects a worktree while a session attaches.

## Cleaning up

Before removal, inspect the worktree state:
- Uncommitted changes or untracked files: `git -c core.fsmonitor= status --porcelain --untracked-files=all`
- New commits since start: `git rev-list --count <base_commit>..HEAD`

The worktree is clean only if all three are absent. If dirty, confirm with
the user before proceeding — removal discards uncommitted work, untracked
files, and new commits.

If the branch pre-existed (not created by this session), confirm separately
before deleting it. Re-check holders — if another session still holds the
worktree, keep it. `cd` out of the worktree before removing (Windows refuses
to delete a process's cwd).

Remove: `git worktree remove --force <path>`, then `git branch -D <name>` if
the branch was created this session or the user confirmed. Delete the claim
record and `rmdir` empty directories (never `rmtree` — a surviving holder
must not be lost).

## Snapshotting

When removing a dirty worktree, save its state to a ref first so the work
is recoverable: stage all files (including untracked, excluding ignored)
and commit to `refs/vibe/reaped/<name>`. Use a separate index file so the
worktree's own index is untouched. Recovery: `git switch -c <name> <ref>`.

## Sweeping

The background sweep removes worktrees whose sessions no longer exist. It
skips claims made within the last 10 minutes, worktrees with active holders,
and worktrees that saved sessions would resume into. Reservations without a
`base_commit` (mkdir claim that never became a worktree) are discarded only
if empty.

Creation, attachment, and retention transitions use a cross-process lock so a
worktree cannot be removed while another process is claiming or attaching it.

## Gotchas

- Disable `core.fsmonitor` when reading someone else's working tree:
  `git -c core.fsmonitor= status`.
- New branches start from the remote default branch, not the current
  checkout's HEAD.
- Symlink paths are rejected — not a stable worktree path.
- `git worktree add` cannot distinguish a taken path from an invalid ref
  (both exit 128), so `mkdir` reserves the path first.
- Worktrees are implicitly trusted for the session only, not persisted.

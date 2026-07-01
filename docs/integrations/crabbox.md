# Crabbox Integration

This guide shows how to pair **Mistral Vibe** (the agent that decides *what* to run) with **[Crabbox](https://crabbox.sh)** (a control plane that decides *where* and *how*). The integration uses only Vibe's existing [Skills](../../README.md#skills-system) mechanism and shells out to the standalone `crabbox` CLI through the built-in `bash` tool. **No changes to Vibe's runtime are required, and Vibe takes no dependency on Crabbox.**

> [!NOTE]
> Community integration, distributed as the Vibe skill below. Crabbox is a third-party product, not affiliated with, endorsed by, vetted, or maintained by Mistral. This page documents a usage pattern only; Mistral does not warrant Crabbox's security, availability, or terms. Use at your own risk.

## When this is useful

Reach for a remote box when a command cannot — or should not — run on the local host:

- tests are slow locally, or need more CPU/RAM than the laptop has;
- the task needs a clean environment, a specific OS, or isolation from local state;
- you want artifacts, screenshots, JUnit summaries, or video as proof of a run;
- the project has CI-only setup steps you do not want to reproduce locally.

For quick local checks, keep using Vibe's built-in `bash` tool — use Crabbox only when remote execution is the point.

## What Crabbox does

Crabbox is a control plane for remote software testing and execution. A run follows a simple lifecycle: **lease a box → sync your dirty checkout → run a command → stream output back → release the box.** It supports fresh leased machines, brokered ready-pool machines (`--pool`), and delegated sandbox providers (E2B, Daytona, Blacksmith Testbox, Azure Dynamic Sessions, Cloudflare) across AWS, Azure, GCP, Hetzner, DigitalOcean, and more. It can also collect run proof (artifacts, screenshots, JUnit summaries, video) and bounds cost with TTLs and spend caps.

The commands this integration relies on:

| Command | Purpose |
|---|---|
| `crabbox login --url <broker>` | Authenticate against a broker (GitHub login; identity is checked on login, and the broker exposes `GET /v1/whoami`). There is no built-in hosted broker — supply `--url` or run `crabbox config set-broker`. |
| `crabbox run -- <command>` | Sync the current dirty checkout to a box, run `<command>`, stream output, and exit with the remote exit code. |
| `crabbox run --id <lease> -- <command>` | Reuse an existing lease by stable ID or friendly slug. |
| `crabbox warmup` | Lease a box and wait until it is ready; keep the lease for reuse. |
| `crabbox job list` / `crabbox job run <name>` | Run a named, repo-defined job (lease routing + optional GitHub Actions hydration + command + cleanup policy). |
| `crabbox ssh --id <lease>` | Print the SSH command for a lease. |
| `crabbox stop <lease>` (alias `release`) | Release a lease or delete a direct-provider machine. |

`crabbox run` builds its sync manifest from `git ls-files --cached --others --exclude-standard` and transfers via rsync over SSH, honoring `.crabboxignore` / `sync.exclude`. Useful flags: `--no-sync`, `--sync-only`, `--pool`, `--ttl`, `--idle-timeout`, `--artifact-glob`, `--require-artifact`, `--junit`, `--emit-proof`.

## The integration model

```text
Vibe (decides WHAT)                Crabbox (decides WHERE / HOW)
  agent loop / skills        --->   lease -> sync -> run -> stream -> release
  tool-permission gating              providers, TTLs, artifacts, pools
```

This split is a framing, not a strict contract: Vibe still owns some *how* (tool-permission gating), and Crabbox `job`s can encode some *what* (a bundled command plus a cleanup policy).

## The Vibe skill

A [skill](../../README.md#skills-system) teaches the agent when and how to call `crabbox` through the existing `bash` tool. Save the file below as `SKILL.md` in a directory named `crabbox` inside any of Vibe's skill discovery locations (the directory name should match the skill `name`; a mismatch is allowed but logs a warning):

1. a path listed in `skill_paths` in `config.toml`;
2. `./.vibe/skills/crabbox/SKILL.md` (project, requires a trusted working directory);
3. `./.agents/skills/crabbox/SKILL.md` (project, requires a trusted working directory);
4. `~/.vibe/skills/crabbox/SKILL.md` (user global);
5. `~/.agents/skills/crabbox/SKILL.md` (user global, Agent Skills standard).

With `user-invocable: true` the skill appears as a `/crabbox` slash command; the agent can also invoke it autonomously when a task matches its `description`.

First install and authenticate the CLI (see <https://crabbox.sh>). Vibe does not distribute or audit the `crabbox` binary — verify the source and review what you are authorizing before running `crabbox login`:

```bash
crabbox login --url https://your-broker.example.com
crabbox doctor   # verify the CLI is healthy
```

Then create the skill:

````markdown
---
name: crabbox
description: >-
  Run shell commands, test suites, or builds on a remote Crabbox box instead of
  locally. Use when a task needs a clean/remote machine, more compute, a
  specific OS, or isolation from the local environment. Wraps the standalone
  `crabbox` CLI (lease a box, sync the dirty checkout, run, stream output,
  release).
license: Apache-2.0
compatibility: Requires the `crabbox` CLI on PATH and an authenticated broker (`crabbox login --url <broker>`).
metadata:
  upstream: https://crabbox.sh
  repo: https://github.com/openclaw/crabbox
user-invocable: true
allowed-tools:
  - bash
---

# Crabbox: run commands on a remote box

Use this skill to execute a command on a short-lived remote machine via Crabbox
rather than on the local host. Crabbox leases a box, syncs the current dirty git
checkout, runs the command, streams output back, and releases the box.

## When to use

- The task needs a clean or remote environment, more compute, a specific OS, or
  isolation from local state.
- The user explicitly asks to "run this on Crabbox / on a box / remotely."

## Preconditions (check before running)

1. `crabbox` is installed and on PATH: `crabbox version`.
2. The CLI is authenticated. If `crabbox doctor` reports an auth problem, stop
   and ask the user to run `crabbox login --url <their-broker-url>`. There is no
   default hosted broker — never guess a broker URL.
3. You are inside a git working tree (Crabbox builds its sync manifest from
   `git ls-files --cached --others --exclude-standard`). Confirm sensitive files
   are excluded via `.crabboxignore` before syncing.
4. Before the first lease or sync, tell the user the exact command you will run
   and that the dirty working tree will be uploaded to a remote machine, then
   get explicit confirmation.

## Run a one-off command

Stream a single command on a fresh leased box. The box is released automatically
when the command finishes, and the local process exits with the remote command's
exit code:

```bash
crabbox run -- <command>
```

Examples:

```bash
crabbox run -- pytest -q
crabbox run -- "make build && make test"
```

## Reuse a warm box

For several commands in a row, warm a box once and reuse the lease:

```bash
crabbox warmup            # prints a lease summary, friendly slug, SSH endpoint
crabbox run --id <lease> -- <command>
crabbox stop <lease>      # release when done (alias: crabbox release)
```

## Named jobs

If the repo defines Crabbox jobs (under the `jobs` key in `crabbox.yaml`):

```bash
crabbox job list
crabbox job run <name>
crabbox job run <name> --dry-run   # print the expanded warmup/run/stop plan
```

## Guardrails

- Confirm before leasing or syncing (see preconditions); the dirty working tree
  is uploaded, so review `.crabboxignore` so you do not ship secrets.
- Always release leases you created with `crabbox warmup` (`crabbox stop`).
- Prefer `--ttl` / `--idle-timeout` so forgotten boxes self-terminate.
- Do not run destructive commands on a box without the user's confirmation.
- If a command fails, surface the remote exit code and streamed output verbatim;
  do not silently retry on a new box.
````

## Safety and cost notes

- Crabbox machines are TTL-bounded with optional spend caps; still prefer `--idle-timeout` / `--ttl` and let leases release automatically.
- The skill should default to confirmation for any action that leases a machine or runs a command.
- Crabbox syncs your *dirty* working tree by default — review `.crabboxignore` so you do not ship secrets or large build output.

## References

- Vibe skills: <https://docs.mistral.ai/vibe/code/cli/skills>
- Crabbox docs: <https://crabbox.sh> — `run`, `warmup`, `job`, `login`
- Crabbox repository: <https://github.com/openclaw/crabbox>

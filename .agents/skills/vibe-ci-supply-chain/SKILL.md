---
name: vibe-ci-supply-chain
description: Git workflow, CI/GitHub Actions, and supply-chain pinning rules for Mistral Vibe. Use when changing CI pipelines, GitHub Actions, dependency pinning, container images, pre-commit hooks, git workflow, or external binary downloads.
metadata:
  display-name: Vibe CI & Supply Chain
  short-description: Git, CI, and supply-chain pinning for Vibe
  default-prompt: Use $vibe-ci-supply-chain to follow Vibe git, CI, and supply-chain conventions.
---

# Vibe CI & Supply Chain

Conventions for git workflow, CI configuration, and supply-chain security in Vibe.

## Startup import cost

CI gates cold-start module count via `vibe/scripts/check_startup_import_cost.py` (budgets in `vibe/scripts/startup_import_cost.vibe.toml`).

- When a change touches imports or module structure on the path of `import vibe` or `from vibe.cli.textual_ui.app import VibeApp`, run `cd vibe && uv run scripts/check_startup_import_cost.py` and confirm the count stays within budget.
- **Verify, do not bump.** An overshoot is a regression to investigate (lazy import, drop the dependency, defer the import) — not a reason to raise the budget. Only widen for a deliberate, PR-justified increase, and set to observed count + ~10% headroom, never the exact count.

## Git

- Never use `git commit --amend`, `git push --force`, or `git push --force-with-lease`.
- Always create new commits and push with a plain `git push`.
- Reconciling with the upstream of the current branch (e.g. push rejected because `origin/<current-branch>` advanced): rebase the current branch onto its upstream — do not merge the upstream branch into the current one, never force-push.
- Reconciling with the base branch (e.g. `origin/main`) once the PR is open: merge the base branch into the current branch — do not rebase, since rebasing rewrites already-pushed history and would require a force-push.
- Run git commands through `uv run` (e.g. `uv run git commit`, `uv run git push`) so pre-commit hooks resolve the project's venv — bare `git commit` fails pre-commit with `reportMissingImports` because pyright can't find third-party packages.

## CI / GitHub Actions

- Pin every `uses:` to a full **commit SHA** with an exact version comment: `uses: owner/action@<commit-sha> # vX.Y.Z`.
- Resolve to the commit, not the annotated-tag object: take the `refs/tags/vX^{}` line from `git ls-remote --tags`, or `gh api repos/<owner>/<repo>/git/refs/tags/<tag> --jq .object` peeled to a commit. Check with `git cat-file -t <sha>` → `commit`, not `tag`. Never pin a moving major tag (`v9`).

## Supply-chain pinning

Every external input to the build, CI, or install path must be pinned to an immutable identifier — never a mutable tag or an unverified download. Add a human-readable comment next to each pin.

- **Container images**: reference by `@sha256:<digest>`, never a bare tag (`:latest`, `:8`). Resolve the digest via the registry's `Docker-Content-Digest` header (`curl -sI -H 'Accept: application/vnd.oci.image.index.v1+json' <registry>/v2/<repo>/manifests/<tag>`). When the image lives inside a JSON matrix string, document the tag→digest mapping in an adjacent comment.
- **pre-commit hooks** (`.pre-commit-config.yaml`): pin every `rev:` to a full commit SHA with a `# vX.Y.Z` comment. Run `pre-commit autoupdate --freeze` to refresh, and resolve to the peeled commit ref (`refs/tags/vX^{}`), not the annotated-tag object — same rule as `uses:` above.
- **Build-system deps** (`pyproject.toml` `[build-system] requires`): pin `hatchling`, `hatch-vcs`, `editables` (and any addition) to exact `==` versions. These execute during source builds and are not covered by `uv.lock`.
- **External binary downloads** (e.g. `patchelf` in `scripts/ci/`): never pipe an unverified download straight into `tar`/`sh`. Download to a temp file, verify `sha256sum -c` against a known-good hash keyed by version (and arch when relevant), then extract. Hard-fail when no hash is registered for the requested version/arch so a bump forces updating the hash.

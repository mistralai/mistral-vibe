# client-e2e

End-to-end tests for the Vibe CLI clients.

## What this is

Drives a built CLI binary as a real user would: it sends terminal input and
app-server JSON-RPC events, then compares the resulting terminal captures.

## Module boundaries

- `e2e/pty/` is product-agnostic. It owns PTY driving, key splitting,
  terminal emulation, and their data types; it must not import `e2e.app_server`.
- `e2e/app_server/` adapts scenarios, Vibe clients, and the replay app server to the
  generic terminal harness. App-server event builders belong here.
- `e2e/pty/capture.py` takes a `Launcher`; `exec_launch` is the default and
  `e2e/app_server/zygote.py` is the Vibe-specific one. The generic layer never
  knows how a client is started.
- `scenarios/` contains declarative timelines whose paths are their identities.
- Keep one authoritative representation per concept; derive replay batches from
  scenario steps and import each concept from its owning module.

## Goal

It is hard to do tests like this for uis, here's why:

- when i press /theme in terminal, the terminal will instantly display /theme but the /theme widget can be display in future rendered frames (a few ms but can become long if we run test in parallel on a cpu) a way to catch this is to use time base waition (ex: wait until the terminal is idle for 0.5 sec, but it is flakky and a lost of time in a lot of cases)
- A solution is to make the tui return an idle event (look at how textual pilot (testing framework) does)
- We should never use timing in those snapshot tests, it will always fail
- The tests should be completely code agnostic
- The tests should concsist of scenarios with just a list of events
  - user bytes input (keys and escaped sequences)
  - app server json rpc events sent to the tui
  - json rpc events expected to be sent to the app server from tui
- There should be a way for the agent to record events (bytes / json rpc events) easily to be able to build good scenarios
- We are mocking completely the server process of the app server here so it should never be launched in tests
- Use pytest to make it easier to parallelize /get failures etc...
- Docstrings and comments must fit on one line.
- Never commit checkout-specific paths in goldens; normalize rendered fixture paths to a stable placeholder.

## The Python zygote

The Python client spends ~0.4s of every capture importing `vibe`. `test_client_e2e.py`
starts one zygote per pytest worker: a process that imports the CLI once, then forks a
child per capture onto a fresh PTY. Set `VIBE_E2E_ZYGOTE=0` to exec instead, or put
`zygote = False` in one scenario to exec only that one.

Because the fork never execs, the CLI must stay fork-safe at import time: no threads,
no event loop, no CoreFoundation. Anything captured at import from the environment must
be reset in `_zygote_server._become_client` (as `PROCESS_START_MONOTONIC` is).
`test_zygote.py` pins forked output to exec'd output.

## Hunting divergences (probe → promote)

Find a Python-vs-Rust discrepancy and pin it as a scenario:

1. Write a throwaway `scenarios/<name>.py`-shaped file with a `timeline` in a scratch dir.
2. `uv run python client-e2e/probe.py <scratch.py>` runs both CLIs, prints `MATCH`/`DIVERGES` + a diff report link.
3. `... --promote <folder>/<name>` copies it into `scenarios/` — only when it diverges (credible-pattern judgment is yours), never overwriting.

Promoted scenarios are auto-discovered by `test_client_e2e.py`; mark a known gap `skip = "<reason>"`. `run.py [name]` does the same capture+report for a committed scenario.

## Golden snapshots (Rust-only)

`test_golden.py` captures the Rust CLI and compares against committed goldens in
`goldens/<scenario>/`. No Python CLI needed — the gate runs Rust-only.

- Each golden stores SVG per snapshot (`snapshot_NN_<label>.svg`) and normalized
  requests (`requests.json`).
- Generate: `make store_golden` (or `uv run --no-project --with pyte --with rich python store_golden.py <scenario>`).
- Test: `make test_golden`.

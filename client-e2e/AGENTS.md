# client-e2e

End-to-end tests for the Vibe CLI clients.

## What this is

Drives the built Rust CLI binary as a real user would: it sends terminal input
and app-server JSON-RPC events, then compares the resulting terminal captures
against committed goldens.

## Module boundaries

- `e2e/pty/` is product-agnostic. It owns PTY driving, key splitting,
  terminal emulation, and their data types; it must not import `e2e.app_server`.
- `e2e/app_server/` adapts scenarios, Vibe clients, and the replay app server to the
  generic terminal harness. App-server event builders belong here.
- `e2e/pty/capture.py` takes a `Launcher`; `exec_launch` is the default. The
  generic layer never knows how a client is started.
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
- Goldens must be environment-independent: reproducible on any machine, checkout, and client version. Any value that varies across environments (version strings, absolute paths, home or temp dirs) must be masked in `e2e/app_server/normalize.py` or pinned by the scenario (frozen fixture, env var, seeded cache) — never stored or hand-bumped per release. `test_golden.py` fails if any golden contains the current `cli-rust/Cargo.toml` version, the checkout path, the home directory, or a harness-generated `e2e_*` temp home; a scenario-pinned path under the shared temp dir is deterministic and passes.

## Adding scenarios

Add a declarative `scenarios/<name>.py` with a `timeline`; it is auto-discovered
by `test_golden.py`. Mark a known gap `skip = "<reason>"`. Generate its golden
with `make store_golden` and review the diff before committing; `test_golden.py`
fails for a scenario without a committed golden and never generates one.

## Golden snapshots

`test_golden.py` captures the Rust CLI and compares against committed goldens in
`goldens/<scenario>/`.

- Each golden stores SVG per snapshot (`snapshot_NN_<label>.svg`) and normalized
  requests (`requests.json`).
- Generate: `make store_golden`. For one scenario: `uv run --no-project --with "pyte==0.8.2" --with "rich==15.0.0" python client-e2e/store_golden.py <scenario>`; unpinned `pyte`/`rich` can render different SVGs.
- Test: `make test_golden`.

## Debugging golden mismatches

- On failure, `test_golden.py` saves the live SVGs under `goldens/_live/<scenario>/` (never committed): `diff goldens/<scenario>/snapshot_NN_<label>.svg goldens/_live/<scenario>/snapshot_NN_<label>.svg` shows the difference. CI keeps the same `_live` SVGs as build artifacts.
- The `make` test targets build the binary like CI, without default features (no `voice`, see `TEST_CARGO_FLAGS` in the Makefile). Compare against goldens rendered from that build, not from a plain `cargo build`.
- Rendering path: the PTY screen is emulated with `pyte`, `e2e/app_server/normalize.py` (`_normalized_cells`) masks volatile cells, then `e2e/app_server/svg.py` renders the SVG with `rich`. Both packages are pinned; other versions render different SVGs.
- A golden that passes on one platform and fails on another usually differs on a single row, often the footer: a value whose width depends on the environment (checkout or home path, PID, version, elapsed time) shifts every cell after it. Fix it in `_normalized_cells` by replacing the whole variable span with a fixed-width placeholder, so later cells keep their columns. Never hand-edit a golden.
- Then regenerate the affected goldens, review the SVG diff, and run `make test_golden`.

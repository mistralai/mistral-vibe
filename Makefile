# vibe-rs build/run helpers for vibe/cli-rust/; run from this dir so `uv run vibe-app-server` resolves.
M = --manifest-path vibe/cli-rust/Cargo.toml
BIN = vibe/cli-rust/target/release/vibe-rs
# Extra cargo flags, e.g. CARGO_BUILD_FLAGS=--no-default-features to drop `voice` and its ALSA dependency.
CARGO_BUILD_FLAGS ?=
# Cargo flags for the tested binary; defaults to --no-default-features to match CI (voice off) so goldens compare against the same feature set.
TEST_CARGO_FLAGS ?= --no-default-features
export VIBE_APP_SERVER_CWD = .

# Local overrides
-include Makefile.local

# Set HARNESS_SRC to a local harness checkout to launch the app-server against its
# editable source instead of the harness pinned in pyproject.toml (path relative to
# VIBE_APP_SERVER_CWD); the guard only activates when that checkout exists.
HARNESS_SRC ?=
HARNESS_EDITABLE = $(if $(HARNESS_SRC),$(if $(wildcard $(HARNESS_SRC)/pyproject.toml),--with-editable $(HARNESS_SRC) ,),)
start run release profile-stress: export VIBE_APP_SERVER_CMD = uv run --quiet $(HARNESS_EDITABLE)vibe-app-server --experimental-harness

# Set QUIET=1 to suppress per-test output (only failures, slowest, and summary).
QUIET ?=
QUIET_FLAGS = $(if $(QUIET),-q,)
CARGO_QUIET = $(if $(QUIET),-- --quiet,)

.PHONY: start run build build_test release fmt lint check clean test test_rust test_golden store_golden profile-stress view-stress

# The golden snapshot pytest run (Rust-only), shared by `test` and `test_golden`.
GOLDEN_CMD = uv run --no-project --with "pyte==0.8.2" --with "rich==15.0.0" --with pytest --with pytest-timeout --with pytest-xdist \
	python -m pytest client-e2e/test_golden.py -n $(E2E_JOBS) --dist loadgroup --timeout 60 $(QUIET_FLAGS)
# Parallel workers (override with E2E_JOBS=N; loadgroup keeps a scenario's steps on one worker).
E2E_JOBS ?= 10

# `make run -- --resume`: make turns the args into goals, so swallow them with no-op rules.
RUN_ARGS = $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
ifneq ($(filter start run release,$(firstword $(MAKECMDGOALS))),)
$(eval $(RUN_ARGS):;@:)
endif

start:          ## Run the prebuilt production binary directly
	$(BIN) $(RUN_ARGS)

run:            ## Debug build + run
	RUST_BACKTRACE=1 cargo run $(M) --bin vibe-rs -- $(RUN_ARGS)

release:        ## Optimized build + run via cargo
	cargo run $(M) --release --bin vibe-rs -- $(RUN_ARGS)

build:          ## Optimized release build only
	cargo build $(M) --release $(CARGO_BUILD_FLAGS)

build_test:     ## Release build in the CI configuration, used by every test target
	cargo build $(M) --release $(TEST_CARGO_FLAGS)

fmt:            ## Format
	cargo fmt $(M)

lint:           ## Clippy, warnings as errors
	cargo clippy $(M) --all-targets $(CARGO_BUILD_FLAGS) -- -D warnings

check: fmt lint ## Format + lint
	cargo check $(M) $(CARGO_BUILD_FLAGS)

clean:
	cargo clean $(M)

test_rust:      ## Rust pure-logic tests (crates/*/tests), fast gate before client-e2e
	cargo test $(M) $(TEST_CARGO_FLAGS) $(CARGO_QUIET)

test: build_test test_rust  ## Rust tests, then golden snapshots
	$(GOLDEN_CMD)

test_golden: build_test ## Rust golden snapshot tests (Rust-only, no Python CLI needed)
	$(GOLDEN_CMD)

store_golden: build_test ## Regenerate Rust golden snapshots (all scenarios)
	uv run --no-project --with "pyte==0.8.2" --with "rich==15.0.0" python client-e2e/store_golden.py --all

STRESS_OUT ?= profile.stress.json

profile-stress: build ## Profile an interactive vibe-rs session with samply (run /stress + scroll, then quit)
	samply record --save-only -o $(STRESS_OUT) -- $(BIN)

view-stress:    ## Open the interactive-session profile in the samply server
	samply load $(STRESS_OUT)

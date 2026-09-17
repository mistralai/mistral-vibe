# vibe-rs build/run helpers for vibe/cli-rust/; run from this dir so `uv run vibe-app-server` resolves.
M = --manifest-path vibe/cli-rust/Cargo.toml
BIN = vibe/cli-rust/target/release/vibe-rs
# Extra cargo flags, e.g. CARGO_BUILD_FLAGS=--no-default-features to drop `voice` and its ALSA dependency.
CARGO_BUILD_FLAGS ?=
# Cargo flags for the tested binary; defaults to --no-default-features to match CI (voice off) so goldens compare against the same feature set.
TEST_CARGO_FLAGS ?= --no-default-features
export VIBE_APP_SERVER_CWD = .

# Set QUIET=1 to suppress per-test output (only failures, slowest, and summary).
QUIET ?=
QUIET_FLAGS = $(if $(QUIET),-q,)
CARGO_QUIET = $(if $(QUIET),-- --quiet,)

.PHONY: start run build build_test release fmt lint check clean test test_rust test_golden store_golden test_flaky profile profile-pyspy profile-samply profile-stress view-pyspy view-samply view-stress

# The client-e2e pytest run, serial so profiling stays meaningful; `test` adds parallelism on top.
E2E_CMD = uv run pytest client-e2e/test_client_e2e.py client-e2e/test_terminal_lifecycle.py
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
	cargo run $(M) --release -- $(RUN_ARGS)

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

test: build_test test_rust  ## Rust tests, then client-e2e parity, then golden snapshots
	$(E2E_CMD) -n $(E2E_JOBS) --dist loadgroup $(QUIET_FLAGS)
	@echo "\n=== Golden snapshots ==="
	$(GOLDEN_CMD)

test_golden: build_test ## Rust golden snapshot tests (Rust-only, no Python CLI needed)
	$(GOLDEN_CMD)

store_golden: build_test ## Regenerate Rust golden snapshots (all scenarios)
	uv run --no-project --with "pyte==0.8.2" --with "rich==15.0.0" python client-e2e/store_golden.py --all

# Flakiness hunt: FLAKY_N parallel runs of the suite; each run's pytest.ini timeout turns a hang into a failure.
FLAKY_N ?= 10
FLAKY_LOGDIR ?= .flaky-logs
test_flaky: build_test   ## Run the client-e2e suite FLAKY_N times in parallel to surface flakiness
	@rm -rf $(FLAKY_LOGDIR); mkdir -p $(FLAKY_LOGDIR)
	@echo "Running client-e2e $(FLAKY_N)x in parallel (logs in $(FLAKY_LOGDIR))..."
	@for i in $$(seq 1 $(FLAKY_N)); do \
		( $(E2E_CMD) -p no:cacheprovider -q >$(FLAKY_LOGDIR)/run$$i.log 2>&1; \
		  echo $$? >$(FLAKY_LOGDIR)/run$$i.rc ) & \
	done; \
	wait; \
	fail=0; \
	for i in $$(seq 1 $(FLAKY_N)); do \
		rc=$$(cat $(FLAKY_LOGDIR)/run$$i.rc); \
		if [ "$$rc" = 0 ]; then echo "run $$i: PASS"; \
		else echo "run $$i: FAIL (rc=$$rc)"; tail -n 8 $(FLAKY_LOGDIR)/run$$i.log; fail=1; fi; \
	done; \
	if [ $$fail = 0 ]; then echo "OK: all $(FLAKY_N) runs passed"; \
	else echo "FLAKY: at least one run failed (see $(FLAKY_LOGDIR))"; exit 1; fi

# Profile targets run only the slowest scenarios; override with PROFILE_K='<pytest -k>' or PROFILE_K= for all.
PROFILE_K ?= input_twenty_shift_enter or edit_diff_theme or combined_selection_scroll or mcp_long_tools
PROFILE_CMD = $(E2E_CMD) $(if $(PROFILE_K),-k "$(PROFILE_K)")

PYSPY_OUT ?= profile.pyspy.speedscope.json
SAMPLY_OUT ?= profile.samply.speedscope.json
STRESS_OUT ?= profile.stress.json

profile: profile-pyspy profile-samply ## Profile the client-e2e pytest run with both py-spy and samply

profile-pyspy:  ## Profile the client-e2e pytest run with py-spy (needs sudo on macOS)
	sudo uv run --with py-spy py-spy record --idle --threads --subprocesses \
		--format speedscope -o $(PYSPY_OUT) \
		-- $(PROFILE_CMD)

profile-samply: ## Profile the client-e2e pytest run with samply
	samply record --save-only -o $(SAMPLY_OUT) -- $(PROFILE_CMD)

view-pyspy:     ## Open the py-spy profile in the speedscope viewer
	npx speedscope $(PYSPY_OUT)

view-samply:    ## Open the samply profile in the samply server
	samply load $(SAMPLY_OUT)

profile-stress: build ## Profile an interactive vibe-rs session with samply (run /stress + scroll, then quit)
	samply record --save-only -o $(STRESS_OUT) -- $(BIN)

view-stress:    ## Open the interactive-session profile in the samply server
	samply load $(STRESS_OUT)

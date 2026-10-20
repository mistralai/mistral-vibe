//! Tests needing process-global isolation; nextest runs each test in its own process.

/// Plain `cargo test` would share one process across these tests: refuse it.
#[ctor::ctor]
fn require_nextest() {
    if std::env::var_os("NEXTEST").is_none() {
        eprintln!("`isolated` needs one process per test: run it with `cargo nextest run`");
        std::process::exit(1);
    }
}

/// Libtest name of test `$name` in the calling module, for helper tests the binary re-runs.
macro_rules! test_name {
    ($name:expr) => {
        format!("{}::{}", module_path!().split_once("::").unwrap().1, $name)
    };
}

#[allow(dead_code)]
#[path = "../onboarding_common/mod.rs"]
mod onboarding_common;

mod ansi_theme_colors;
mod app_server_disconnect;
mod app_server_lifecycle;
mod app_server_oversized_frame;
mod app_server_process_group;
mod app_server_stderr_drain;
mod app_server_stderr_level;
mod argument_errors;
mod clipboard;
mod clipboard_copies;
mod clipboard_fail_closed;
mod completion_manager;
mod composer_cursor_line;
mod composer_history_edits;
mod composer_history_ownership;
mod config_editor;
mod cron_effect;
mod crossterm_keys;
mod deleted_working_directory;
mod dotenv_child_env;
mod dotenv_env;
mod edit_history;
mod execution_control_flags;
mod file_index;
mod greeting_cache;
mod headless_history_paging;
mod headless_signal;
mod headless_streaming_history_failure;
mod history_manager;
mod interactive_terminal;
mod loop_routing;
mod markdown_widget;
mod mcp_cli;
mod mcp_filter;
mod observability_file_logging;
mod observability_sentry;
mod onboarding_boot;
mod onboarding_context;
mod onboarding_custom_domain_inputs;
mod onboarding_dual_domain;
mod onboarding_fake_server;
mod onboarding_input_theme;
mod onboarding_mouse;
mod onboarding_override_confirm;
mod onboarding_round;
mod onboarding_setup_scenarios;
mod pending_commands_ready;
mod piped_stdin;
mod remote_project;
mod remote_project_lifecycle;
mod resync_backend;
mod runtime_config_refresh;
mod server_notices;
mod session_exit;
mod shutdown_signal;
mod startup_banners;
mod startup_cache;
mod startup_cache_disk;
mod startup_telemetry;
mod startup_telemetry_ready;
mod subagent_notification;
mod suspend_key;
mod suspend_lifecycle;
mod tab_cells;
mod terminal_disconnect;
mod terminal_init;
mod terminal_notifier;
mod theme_picker_state;
mod tls_custom_roots;
mod todo_tracker;
mod transcript_snapshot_order;
mod trust_folder_copy;
mod trust_folder_keys;
mod trust_folder_margin;
mod trust_folder_selection;
mod tts_trust;
mod update_cache;
mod update_pypi_gateway;
mod update_use_case;
mod voice_settings_cache;
mod voice_settings_draft;
mod whats_new_cache;
mod whats_new_dismiss;
mod windows_child;
mod workdir;
mod worktree;
mod zero_width_graphemes;

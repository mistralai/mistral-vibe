//! Tests needing process-global isolation; nextest runs each test in its own process.
#![allow(clippy::disallowed_methods)] // Tests drive tasks directly; replay acks never run here.

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
mod clipboard_linux;
mod clipboard_tmux;
mod completion_manager;
mod composer_cursor_line;
mod config_editor;
mod crash_report_dsn_handoff;
mod deleted_working_directory;
mod dotenv_child_env;
mod dotenv_env;
mod file_index;
mod headless_denials;
mod headless_history_paging;
mod headless_open_stdin;
mod headless_signal;
mod headless_streaming_history_failure;
mod headless_turn_events_before_reply;
mod history_manager;
mod interactive_terminal;
mod job_control;
mod markdown_colors;
mod markdown_widget;
mod mcp_cli;
mod observability_file_logging;
mod observability_sentry;
mod oidc_endpoints;
mod onboarding_boot;
mod onboarding_custom_domain_inputs;
mod onboarding_dual_domain;
mod onboarding_fake_server;
mod onboarding_input_theme;
mod onboarding_mouse;
mod onboarding_oidc;
mod onboarding_override_confirm;
mod onboarding_round;
mod onboarding_setup_scenarios;
mod pending_commands_ready;
mod piped_stdin;
mod rail_last_use;
mod replay_clock;
mod runtime_config_refresh;
mod server_multiplex;
mod session_exit;
mod shutdown_signal;
mod startup_banners;
mod startup_cache_disk;
mod startup_failure_report;
mod startup_home_warning;
mod startup_telemetry;
mod startup_telemetry_ready;
mod suspend_lifecycle;
mod terminal_disconnect;
mod terminal_init;
mod theme_picker_state;
mod tls_custom_roots;
mod tmux_mouse;
mod tmux_mouse_hint;
mod tts_trust;
mod update_cache;
mod update_pypi_gateway;
mod update_use_case;
mod update_uv_oracle_fallback;
mod update_uv_system_certs;
mod voice_settings_cache;
mod voice_settings_draft;
mod whats_new_cache;
mod windows_child;
mod windows_mouse_capture;
mod windows_vt_input;
mod work_tracking;
mod workdir;

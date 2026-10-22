//! Pure logic tests share one integration-test binary.

/// Keep the units binary off the host clipboard: install the null sink.
#[ctor::ctor]
fn isolate_host_clipboard() {
    vibe_rs::clipboard::set_sink(std::sync::Arc::new(vibe_rs::clipboard::NullClipboard));
}

#[path = "units/agent_socket.rs"]
mod agent_socket;
#[path = "units/agent_switching.rs"]
mod agent_switching;
#[path = "units/app_server_crash.rs"]
mod app_server_crash;
#[path = "units/approval_keys.rs"]
mod approval_keys;
#[path = "units/argument_hint.rs"]
mod argument_hint;
#[path = "units/banner.rs"]
mod banner;
#[path = "units/bottom_bar_overflow.rs"]
mod bottom_bar_overflow;
#[path = "units/bottom_bar_selection.rs"]
mod bottom_bar_selection;
#[path = "units/builtin_skill_commands.rs"]
mod builtin_skill_commands;
#[path = "units/callback_kinds.rs"]
mod callback_kinds;
#[path = "units/caret_focus.rs"]
mod caret_focus;
#[path = "units/check_upgrade_flags.rs"]
mod check_upgrade_flags;
#[path = "units/clear_history.rs"]
mod clear_history;
#[path = "units/click_chain.rs"]
mod click_chain;
#[path = "units/clipboard_isolation.rs"]
mod clipboard_isolation;
#[path = "units/collapsed_pastes.rs"]
mod collapsed_pastes;
#[path = "units/color_literals.rs"]
mod color_literals;
#[path = "units/color_system.rs"]
mod color_system;
#[path = "units/command_parse.rs"]
mod command_parse;
#[path = "units/command_result_layout.rs"]
mod command_result_layout;
#[path = "units/command_submission.rs"]
mod command_submission;
#[path = "units/compact_handoff.rs"]
mod compact_handoff;
#[path = "units/compaction_checkpoint.rs"]
mod compaction_checkpoint;
#[path = "units/completion_popup.rs"]
mod completion_popup;
#[path = "units/composer_blur.rs"]
mod composer_blur;
#[path = "units/composer_clicks.rs"]
mod composer_clicks;
#[path = "units/composer_mentions.rs"]
mod composer_mentions;
#[path = "units/composer_pastes.rs"]
mod composer_pastes;
#[path = "units/config_filter.rs"]
mod config_filter;
#[path = "units/config_issues.rs"]
mod config_issues;
#[path = "units/config_selection.rs"]
mod config_selection;
#[path = "units/config_write.rs"]
mod config_write;
#[path = "units/connector_web.rs"]
mod connector_web;
#[path = "units/count_format.rs"]
mod count_format;
#[path = "units/credentials.rs"]
mod credentials;
#[path = "units/debug_console.rs"]
mod debug_console;
#[path = "units/dotenv.rs"]
mod dotenv;
#[path = "units/double_escape.rs"]
mod double_escape;
#[path = "units/effect_entry.rs"]
mod effect_entry;
#[path = "units/effect_output.rs"]
mod effect_output;
#[path = "units/external_editor.rs"]
mod external_editor;
#[path = "units/file_index_metrics.rs"]
mod file_index_metrics;
#[path = "units/file_index_rescan_ignore.rs"]
mod file_index_rescan_ignore;
#[path = "units/file_mention_matching.rs"]
mod file_mention_matching;
#[path = "units/focus.rs"]
mod focus;
#[path = "units/fold_clicks.rs"]
mod fold_clicks;
#[path = "units/foreign_session.rs"]
mod foreign_session;
#[path = "units/frames_oversized.rs"]
mod frames_oversized;
#[path = "units/headless_cli_flags.rs"]
mod headless_cli_flags;
#[path = "units/headless_logic.rs"]
mod headless_logic;
#[path = "units/headless_prompt.rs"]
mod headless_prompt;
#[path = "units/headless_run.rs"]
mod headless_run;
#[path = "units/headless_startup.rs"]
mod headless_startup;
#[path = "units/hints.rs"]
mod hints;
#[path = "units/history_loading.rs"]
mod history_loading;
#[path = "units/history_wrapped_recall.rs"]
mod history_wrapped_recall;
#[path = "units/image_placeholders.rs"]
mod image_placeholders;
#[path = "units/image_prompt.rs"]
mod image_prompt;
#[path = "units/incomplete_stream_retry.rs"]
mod incomplete_stream_retry;
#[path = "units/inline_images.rs"]
mod inline_images;
#[path = "units/input_edit_keys.rs"]
mod input_edit_keys;
#[path = "units/input_line_edges.rs"]
mod input_line_edges;
#[path = "units/input_mode_edges.rs"]
mod input_mode_edges;
#[path = "units/input_modes.rs"]
mod input_modes;
#[path = "units/interactive_agent_config.rs"]
mod interactive_agent_config;
#[path = "units/key_burst.rs"]
mod key_burst;
#[path = "units/launch.rs"]
mod launch;
#[path = "units/leanstall.rs"]
mod leanstall;
#[path = "units/linked_lines_gaps.rs"]
mod linked_lines_gaps;
#[path = "units/list_nav.rs"]
mod list_nav;
#[path = "units/list_scroll.rs"]
mod list_scroll;
#[path = "units/loading_frames.rs"]
mod loading_frames;
#[path = "units/loading_startup.rs"]
mod loading_startup;
#[path = "units/long_paste.rs"]
mod long_paste;
#[path = "units/markdown_cache.rs"]
mod markdown_cache;
#[path = "units/markdown_links.rs"]
mod markdown_links;
#[path = "units/markdown_parse.rs"]
mod markdown_parse;
#[path = "units/mcp_add_args.rs"]
mod mcp_add_args;
#[path = "units/mcp_add_help.rs"]
mod mcp_add_help;
#[path = "units/mcp_browser.rs"]
mod mcp_browser;
#[path = "units/mcp_cli_parity.rs"]
mod mcp_cli_parity;
#[path = "units/mcp_oauth.rs"]
mod mcp_oauth;
#[path = "units/mcp_open.rs"]
mod mcp_open;
#[path = "units/message_queue.rs"]
mod message_queue;
#[path = "units/message_queue_delivery.rs"]
mod message_queue_delivery;
#[path = "units/message_queue_replacement.rs"]
mod message_queue_replacement;
#[path = "units/message_queue_serialization.rs"]
mod message_queue_serialization;
#[path = "units/misc_state.rs"]
mod misc_state;
#[path = "units/mode_indicator.rs"]
mod mode_indicator;
#[path = "units/model_enforcement.rs"]
mod model_enforcement;
#[path = "units/model_picker.rs"]
mod model_picker;
#[path = "units/mouse_pixels.rs"]
mod mouse_pixels;
#[path = "units/mouse_routing.rs"]
mod mouse_routing;
#[path = "units/mutation_runtime.rs"]
mod mutation_runtime;
#[path = "units/narrator_audio.rs"]
mod narrator_audio;
#[path = "units/narrator_status.rs"]
mod narrator_status;
#[path = "units/notice_state.rs"]
mod notice_state;
#[path = "units/observability_memory.rs"]
mod observability_memory;
#[path = "units/observability_rotating.rs"]
mod observability_rotating;
#[path = "units/observability_scrub.rs"]
mod observability_scrub;
#[path = "units/older_history.rs"]
mod older_history;
#[path = "units/onboarding_boot_pending.rs"]
mod onboarding_boot_pending;
#[path = "units/onboarding_loop_guard.rs"]
mod onboarding_loop_guard;
#[path = "units/onboarding_screens.rs"]
mod onboarding_screens;
#[path = "units/onboarding_seed.rs"]
mod onboarding_seed;
#[path = "units/onboarding_sign_in_flow.rs"]
mod onboarding_sign_in_flow;
#[path = "units/onboarding_small_terminal.rs"]
mod onboarding_small_terminal;
#[path = "units/onboarding_submit_choices.rs"]
mod onboarding_submit_choices;
#[path = "units/onboarding_whoami.rs"]
mod onboarding_whoami;
#[path = "units/option_clicks.rs"]
mod option_clicks;
#[path = "units/option_hover.rs"]
mod option_hover;
#[path = "units/option_list.rs"]
mod option_list;
#[path = "units/paste_image.rs"]
mod paste_image;
#[path = "units/paste_path.rs"]
mod paste_path;
#[path = "units/paste_path_list.rs"]
mod paste_path_list;
#[path = "units/paste_probe.rs"]
mod paste_probe;
#[path = "units/pending_commands.rs"]
mod pending_commands;
#[path = "units/plugins.rs"]
mod plugins;
#[path = "units/pointer_shape.rs"]
mod pointer_shape;
#[path = "units/provider_auth.rs"]
mod provider_auth;
#[path = "units/proxy_setup.rs"]
mod proxy_setup;
#[path = "units/question_app.rs"]
mod question_app;
#[path = "units/question_app_answers.rs"]
mod question_app_answers;
#[path = "units/question_app_free_text.rs"]
mod question_app_free_text;
#[path = "units/question_app_hide_other.rs"]
mod question_app_hide_other;
#[path = "units/question_app_input_keymap.rs"]
mod question_app_input_keymap;
#[path = "units/question_app_navigation.rs"]
mod question_app_navigation;
#[path = "units/question_app_number_keys.rs"]
mod question_app_number_keys;
#[path = "units/question_app_other_bar.rs"]
mod question_app_other_bar;
#[path = "units/question_app_paste.rs"]
mod question_app_paste;
#[path = "units/question_app_prefix_chrome.rs"]
mod question_app_prefix_chrome;
#[path = "units/question_app_space_toggle.rs"]
mod question_app_space_toggle;
#[path = "units/queue_feedback_session.rs"]
mod queue_feedback_session;
#[path = "units/queue_hints.rs"]
mod queue_hints;
#[path = "units/queue_images.rs"]
mod queue_images;
#[path = "units/queue_paused_after_interrupt.rs"]
mod queue_paused_after_interrupt;
#[path = "units/queue_selection_flow.rs"]
mod queue_selection_flow;
#[path = "units/queue_spacer.rs"]
mod queue_spacer;
#[path = "units/queue_steer_failures.rs"]
mod queue_steer_failures;
#[path = "units/quit_confirmation.rs"]
mod quit_confirmation;
#[path = "units/quit_manager_state.rs"]
mod quit_manager_state;
#[path = "units/reader_frame_split.rs"]
mod reader_frame_split;
#[path = "units/rename_usage.rs"]
mod rename_usage;
#[path = "units/replay_drained.rs"]
mod replay_drained;
#[path = "units/resume_agent_config.rs"]
mod resume_agent_config;
#[path = "units/resume_attempts.rs"]
mod resume_attempts;
#[path = "units/resume_lease_lock.rs"]
mod resume_lease_lock;
#[path = "units/resume_picker_delete.rs"]
mod resume_picker_delete;
#[path = "units/resume_picker_exits.rs"]
mod resume_picker_exits;
#[path = "units/resume_picker_outcomes.rs"]
mod resume_picker_outcomes;
#[path = "units/retry_presentation.rs"]
mod retry_presentation;
#[path = "units/retry_snapshot_merge.rs"]
mod retry_snapshot_merge;
#[path = "units/rewind.rs"]
mod rewind;
#[path = "units/rpc_contract.rs"]
mod rpc_contract;
#[path = "units/rpc_error_frame.rs"]
mod rpc_error_frame;
#[path = "units/rpc_error_session_busy.rs"]
mod rpc_error_session_busy;
#[path = "units/run_export.rs"]
mod run_export;
#[path = "units/run_export_subagents.rs"]
mod run_export_subagents;
#[path = "units/runtime_refresh.rs"]
mod runtime_refresh;
#[path = "units/scheduled_loop_fired.rs"]
mod scheduled_loop_fired;
#[path = "units/scroll_anchor.rs"]
mod scroll_anchor;
#[path = "units/scrollbar_drag.rs"]
mod scrollbar_drag;
#[path = "units/scrollbar_track_click.rs"]
mod scrollbar_track_click;
#[path = "units/search_field.rs"]
mod search_field;
#[path = "units/selection_folds.rs"]
mod selection_folds;
#[path = "units/selection_lost_release.rs"]
mod selection_lost_release;
#[path = "units/selection_prompt_marker.rs"]
mod selection_prompt_marker;
#[path = "units/selection_spans.rs"]
mod selection_spans;
#[path = "units/setup_probe.rs"]
mod setup_probe;
#[path = "units/setup_welcome_gate.rs"]
mod setup_welcome_gate;
#[path = "units/setup_wire.rs"]
mod setup_wire;
#[path = "units/shell_submission.rs"]
mod shell_submission;
#[path = "units/skill_mention_completion.rs"]
mod skill_mention_completion;
#[path = "units/slash_completion.rs"]
mod slash_completion;
#[path = "units/startup_resume_intent.rs"]
mod startup_resume_intent;
#[path = "units/stress_command.rs"]
mod stress_command;

#[path = "units/subagent_click.rs"]
mod subagent_click;
#[path = "units/subagent_list.rs"]
mod subagent_list;
#[path = "units/subagent_sessions.rs"]
mod subagent_sessions;
#[path = "units/subagent_transcript.rs"]
mod subagent_transcript;
#[path = "units/subagent_view_keys.rs"]
mod subagent_view_keys;
#[path = "units/telemetry_events.rs"]
mod telemetry_events;
#[path = "units/teleport_input.rs"]
mod teleport_input;
#[path = "units/teleport_lifecycle.rs"]
mod teleport_lifecycle;
#[path = "units/teleport_picker.rs"]
mod teleport_picker;
#[path = "units/teleport_support.rs"]
mod teleport_support;
#[path = "units/terminal_detect.rs"]
mod terminal_detect;
#[path = "units/terminal_input_filter.rs"]
mod terminal_input_filter;
#[path = "units/terminal_read.rs"]
mod terminal_read;
#[path = "units/theme_detection.rs"]
mod theme_detection;
#[path = "units/thinking_picker.rs"]
mod thinking_picker;
#[path = "units/toast_anchor.rs"]
mod toast_anchor;
#[path = "units/toast_selection.rs"]
mod toast_selection;
#[path = "units/todo_sidebar_close.rs"]
mod todo_sidebar_close;
#[path = "units/toggle_scroll.rs"]
mod toggle_scroll;
#[path = "units/tool_group_membership.rs"]
mod tool_group_membership;
#[path = "units/transcript_diff.rs"]
mod transcript_diff;
#[path = "units/transcript_images.rs"]
mod transcript_images;
#[path = "units/transcript_patch.rs"]
mod transcript_patch;
#[path = "units/transcript_tool_group.rs"]
mod transcript_tool_group;
#[path = "units/tts_request.rs"]
mod tts_request;
#[path = "units/turn_counter.rs"]
mod turn_counter;
#[path = "units/turn_error_message.rs"]
mod turn_error_message;
#[path = "units/unsolicited_turn.rs"]
mod unsolicited_turn;
#[path = "units/update_brew_oracle.rs"]
mod update_brew_oracle;
#[path = "units/update_prompt.rs"]
mod update_prompt;

#[path = "units/program_status.rs"]
mod program_status;
#[path = "units/program_status_detection.rs"]
mod program_status_detection;
#[path = "units/program_status_regressions.rs"]
mod program_status_regressions;
#[path = "units/terminal_late_completion.rs"]
mod terminal_late_completion;
#[path = "units/terminal_lifecycle.rs"]
mod terminal_lifecycle;
#[path = "units/update_uv_oracle.rs"]
mod update_uv_oracle;
#[path = "units/update_version.rs"]
mod update_version;
#[path = "units/version_flag.rs"]
mod version_flag;
#[path = "units/voice_errors.rs"]
mod voice_errors;
#[path = "units/voice_settings.rs"]
mod voice_settings;
#[path = "units/voice_text.rs"]
mod voice_text;
#[path = "units/voice_tracking.rs"]
mod voice_tracking;
#[path = "units/whoami.rs"]
mod whoami;

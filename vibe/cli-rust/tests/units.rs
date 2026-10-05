//! Pure logic tests share one integration-test binary.

#[path = "units/app_server_crash.rs"]
mod app_server_crash;
#[path = "units/argument_hint.rs"]
mod argument_hint;
#[path = "units/bottom_bar_overflow.rs"]
mod bottom_bar_overflow;
#[path = "units/bottom_bar_selection.rs"]
mod bottom_bar_selection;
#[path = "units/callback_kinds.rs"]
mod callback_kinds;
#[path = "units/caret_focus.rs"]
mod caret_focus;
#[path = "units/check_upgrade_flags.rs"]
mod check_upgrade_flags;
#[path = "units/clear_history.rs"]
mod clear_history;
#[path = "units/collapsed_pastes.rs"]
mod collapsed_pastes;
#[path = "units/command_result_layout.rs"]
mod command_result_layout;
#[path = "units/compact_handoff.rs"]
mod compact_handoff;
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
#[path = "units/connector_web.rs"]
mod connector_web;
#[path = "units/credentials.rs"]
mod credentials;
#[path = "units/dotenv.rs"]
mod dotenv;
#[path = "units/effect_entry.rs"]
mod effect_entry;
#[path = "units/effect_output.rs"]
mod effect_output;
#[path = "units/file_mention_matching.rs"]
mod file_mention_matching;
#[path = "units/foreign_session.rs"]
mod foreign_session;
#[path = "units/headless_cli_flags.rs"]
mod headless_cli_flags;
#[path = "units/headless_logic.rs"]
mod headless_logic;
#[path = "units/headless_prompt.rs"]
mod headless_prompt;
#[path = "units/history_loading.rs"]
mod history_loading;
#[path = "units/image_placeholders.rs"]
mod image_placeholders;
#[path = "units/image_prompt.rs"]
mod image_prompt;
#[path = "units/inline_images.rs"]
mod inline_images;
#[path = "units/input_edit_keys.rs"]
mod input_edit_keys;
#[path = "units/input_modes.rs"]
mod input_modes;
#[path = "units/interactive_agent_config.rs"]
mod interactive_agent_config;
#[path = "units/launch.rs"]
mod launch;
#[path = "units/long_paste.rs"]
mod long_paste;
#[path = "units/markdown_cache.rs"]
mod markdown_cache;
#[path = "units/markdown_links.rs"]
mod markdown_links;
#[path = "units/markdown_parse.rs"]
mod markdown_parse;
#[path = "units/mcp_browser.rs"]
mod mcp_browser;
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
#[path = "units/mouse_routing.rs"]
mod mouse_routing;
#[path = "units/mutation_runtime.rs"]
mod mutation_runtime;
#[path = "units/notice_state.rs"]
mod notice_state;
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
#[path = "units/pointer_shape.rs"]
mod pointer_shape;
#[path = "units/provider_auth.rs"]
mod provider_auth;
#[path = "units/question_app.rs"]
mod question_app;
#[path = "units/question_app_hide_other.rs"]
mod question_app_hide_other;
#[path = "units/question_app_input_keymap.rs"]
mod question_app_input_keymap;
#[path = "units/question_app_paste.rs"]
mod question_app_paste;
#[path = "units/question_app_prefix_chrome.rs"]
mod question_app_prefix_chrome;
#[path = "units/queue_feedback_session.rs"]
mod queue_feedback_session;
#[path = "units/queue_images.rs"]
mod queue_images;
#[path = "units/quit_confirmation.rs"]
mod quit_confirmation;
#[path = "units/reader_frame_split.rs"]
mod reader_frame_split;
#[path = "units/resume_agent_config.rs"]
mod resume_agent_config;
#[path = "units/retry_presentation.rs"]
mod retry_presentation;
#[path = "units/retry_snapshot_merge.rs"]
mod retry_snapshot_merge;
#[path = "units/rpc_contract.rs"]
mod rpc_contract;
#[path = "units/rpc_error_frame.rs"]
mod rpc_error_frame;
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
#[path = "units/startup_resume_intent.rs"]
mod startup_resume_intent;
#[path = "units/stress_command.rs"]
mod stress_command;
#[path = "units/subagent_list.rs"]
mod subagent_list;
#[path = "units/subagent_sessions.rs"]
mod subagent_sessions;
#[path = "units/subagent_transcript.rs"]
mod subagent_transcript;
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
#[path = "units/theme_detection.rs"]
mod theme_detection;
#[path = "units/thinking_picker.rs"]
mod thinking_picker;
#[path = "units/toast_selection.rs"]
mod toast_selection;
#[path = "units/todo_sidebar_close.rs"]
mod todo_sidebar_close;
#[path = "units/toggle_scroll.rs"]
mod toggle_scroll;
#[path = "units/transcript_diff.rs"]
mod transcript_diff;
#[path = "units/transcript_images.rs"]
mod transcript_images;
#[path = "units/transcript_patch.rs"]
mod transcript_patch;
#[path = "units/transcript_tool_group.rs"]
mod transcript_tool_group;
#[path = "units/turn_error_message.rs"]
mod turn_error_message;
#[path = "units/update_brew_oracle.rs"]
mod update_brew_oracle;
#[path = "units/update_prompt.rs"]
mod update_prompt;

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

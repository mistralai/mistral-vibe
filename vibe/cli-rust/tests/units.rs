//! Pure logic tests share one integration-test binary.

#[path = "units/app_server_crash.rs"]
mod app_server_crash;
#[path = "units/bottom_bar_overflow.rs"]
mod bottom_bar_overflow;
#[path = "units/bottom_bar_selection.rs"]
mod bottom_bar_selection;
#[path = "units/callback_kinds.rs"]
mod callback_kinds;
#[path = "units/caret_focus.rs"]
mod caret_focus;
#[path = "units/compact_handoff.rs"]
mod compact_handoff;
#[path = "units/config_filter.rs"]
mod config_filter;
#[path = "units/config_issues.rs"]
mod config_issues;
#[path = "units/config_selection.rs"]
mod config_selection;
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
#[path = "units/image_prompt.rs"]
mod image_prompt;
#[path = "units/input_edit_keys.rs"]
mod input_edit_keys;
#[path = "units/input_modes.rs"]
mod input_modes;
#[path = "units/interactive_agent_config.rs"]
mod interactive_agent_config;
#[path = "units/launch.rs"]
mod launch;
#[path = "units/markdown_cache.rs"]
mod markdown_cache;
#[path = "units/markdown_parse.rs"]
mod markdown_parse;
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
#[path = "units/notice_state.rs"]
mod notice_state;
#[path = "units/observability_rotating.rs"]
mod observability_rotating;
#[path = "units/observability_scrub.rs"]
mod observability_scrub;
#[path = "units/paste_image.rs"]
mod paste_image;
#[path = "units/paste_path.rs"]
mod paste_path;
#[path = "units/pending_commands.rs"]
mod pending_commands;
#[path = "units/pointer_shape.rs"]
mod pointer_shape;
#[path = "units/question_app.rs"]
mod question_app;
#[path = "units/question_app_hide_other.rs"]
mod question_app_hide_other;
#[path = "units/question_app_paste.rs"]
mod question_app_paste;
#[path = "units/question_app_prefix_chrome.rs"]
mod question_app_prefix_chrome;
#[path = "units/queue_feedback_session.rs"]
mod queue_feedback_session;
#[path = "units/queue_images.rs"]
mod queue_images;
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
#[path = "units/scroll_anchor.rs"]
mod scroll_anchor;
#[path = "units/scrollbar_drag.rs"]
mod scrollbar_drag;
#[path = "units/selection_spans.rs"]
mod selection_spans;
#[path = "units/shell_submission.rs"]
mod shell_submission;
#[path = "units/startup_resume_intent.rs"]
mod startup_resume_intent;
#[path = "units/stress_command.rs"]
mod stress_command;
#[path = "units/terminal_input_filter.rs"]
mod terminal_input_filter;
#[path = "units/theme_detection.rs"]
mod theme_detection;
#[path = "units/toast_selection.rs"]
mod toast_selection;
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
#[path = "units/version_flag.rs"]
mod version_flag;

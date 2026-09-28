use serde_json::Value;

use crate::core::error::CoreError;
use crate::core::tools::external::{ExternalTool, ExternalToolCall};

use super::descriptor::TypeScriptTool;
use super::settings::Settings;

/// One program operation resolved against the same immutable session snapshot
/// as the descriptors supplied to the evaluator.
pub(crate) struct ResolvedProgramOperation {
    pub(super) tool: ExternalTool,
    pub(super) pre_hook_binding_ids: Vec<String>,
}

impl ResolvedProgramOperation {
    pub(crate) fn new(tool: ExternalTool, pre_hook_binding_ids: Vec<String>) -> Self {
        Self {
            tool,
            pre_hook_binding_ids,
        }
    }
}

/// The session projection needed to start or resume private TypeScript execution.
///
/// The session coordinator builds this value from its resolved tools and hook
/// index. The feature only sees its own settings and program
/// descriptors plus narrow callbacks for resolving operations, selecting
/// post-tool hooks, and validating hook-rewritten arguments. Tool discovery is
/// dispatched before program execution and is never retained by replay
/// continuations.
#[derive(Clone, Copy)]
pub(crate) struct ProgramContext<'a> {
    descriptors: &'a [TypeScriptTool],
    settings: &'a Settings,
    turn_id: &'a str,
    message_count: usize,
    resolve_operation: &'a dyn Fn(&str, Value) -> Option<ResolvedProgramOperation>,
    post_hook_binding_ids: &'a dyn Fn(&ExternalToolCall) -> Vec<String>,
    effective_call: &'a dyn Fn(&ExternalToolCall, Value) -> Result<ExternalToolCall, CoreError>,
}

impl<'a> ProgramContext<'a> {
    pub(crate) fn new(
        descriptors: &'a [TypeScriptTool],
        settings: &'a Settings,
        turn_id: &'a str,
        message_count: usize,
        resolve_operation: &'a dyn Fn(&str, Value) -> Option<ResolvedProgramOperation>,
        post_hook_binding_ids: &'a dyn Fn(&ExternalToolCall) -> Vec<String>,
        effective_call: &'a dyn Fn(&ExternalToolCall, Value) -> Result<ExternalToolCall, CoreError>,
    ) -> Self {
        Self {
            descriptors,
            settings,
            turn_id,
            message_count,
            resolve_operation,
            post_hook_binding_ids,
            effective_call,
        }
    }

    pub(crate) fn descriptors(self) -> &'a [TypeScriptTool] {
        self.descriptors
    }

    pub(crate) fn settings(self) -> &'a Settings {
        self.settings
    }

    pub(crate) fn turn_id(self) -> &'a str {
        self.turn_id
    }

    pub(crate) fn message_count(self) -> usize {
        self.message_count
    }

    pub(crate) fn resolve_operation(
        self,
        name: &str,
        arguments: Value,
    ) -> Option<ResolvedProgramOperation> {
        (self.resolve_operation)(name, arguments)
    }

    pub(crate) fn post_hook_binding_ids(self, call: &ExternalToolCall) -> Vec<String> {
        (self.post_hook_binding_ids)(call)
    }

    pub(crate) fn effective_call(
        self,
        original: &ExternalToolCall,
        arguments: Value,
    ) -> Result<ExternalToolCall, CoreError> {
        (self.effective_call)(original, arguments)
    }
}

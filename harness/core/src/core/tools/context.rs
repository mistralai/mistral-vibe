use crate::core::capabilities::HookBindingIndex;
use crate::core::error::CoreError;
use crate::core::features::large_output;
use crate::core::features::programmatic_tool_calling::{ProgramContext, ResolvedProgramOperation};
use crate::core::features::tool_discovery::{SearchRequest, ToolDiscoveryResult};
use crate::core::hooks::HookPoint;
use crate::core::hooks::HookToolKey;
use crate::core::tools::external::{ExternalTool, ExternalToolCall};
use crate::core::tools::resolved::ResolvedTools;
use crate::core::wire::tool::ToolCall;
use serde_json::Value;

/// Everything dispatching a tool needs from the session, and nothing else.
///
/// Routing a call depends on the effective capabilities and hook points;
/// attributing the resulting action needs the turn. Model context and turn
/// phase are deliberately absent, so a tool path cannot reach the conversation
/// or the state machine — it can only describe work for the Runtime to do.
#[derive(Clone, Copy)]
pub(crate) struct ToolContext<'a> {
    tools: &'a ResolvedTools,
    hook_binding_index: &'a HookBindingIndex,
    pub(crate) turn_id: &'a str,
    /// Context length at dispatch time. Seeds replay-stable execution ids.
    pub(crate) message_count: usize,
}

impl<'a> ToolContext<'a> {
    pub(crate) fn new(
        tools: &'a ResolvedTools,
        hook_binding_index: &'a HookBindingIndex,
        turn_id: &'a str,
        message_count: usize,
    ) -> Self {
        Self {
            tools,
            hook_binding_index,
            turn_id,
            message_count,
        }
    }

    pub(crate) fn resolve_direct_call(&self, call: &ToolCall) -> Result<ExternalTool, CoreError> {
        self.tools.resolve_direct_call(call)
    }

    pub(crate) fn search(&self, request: SearchRequest) -> ToolDiscoveryResult {
        self.tools.search_with_summary(request)
    }

    pub(crate) fn large_output_policy(&self) -> &large_output::Policy {
        self.tools.large_output_policy()
    }

    pub(crate) fn hook_binding_ids(
        &self,
        point: HookPoint,
        tool_key: Option<&HookToolKey>,
    ) -> Vec<String> {
        self.hook_binding_index.select(point, tool_key)
    }

    pub(crate) fn effective_call(
        &self,
        original: &ExternalToolCall,
        arguments: Value,
    ) -> Result<ExternalToolCall, CoreError> {
        self.tools.resolve_effective_call(original, arguments)
    }

    pub(crate) fn with_program_context<R>(&self, apply: impl FnOnce(ProgramContext<'_>) -> R) -> R {
        let resolve_operation = |name: &str, arguments: Value| {
            let tool = self.tools.resolve_program_call(name, arguments)?;
            let pre_hook_binding_ids =
                self.hook_binding_ids(HookPoint::PreToolCall, Some(&tool.hook_tool_key()));
            Some(ResolvedProgramOperation::new(tool, pre_hook_binding_ids))
        };
        let post_hook_binding_ids = |call: &ExternalToolCall| {
            self.hook_binding_ids(HookPoint::PostToolCall, Some(&call.hook_tool_key()))
        };
        let effective_call = |original: &ExternalToolCall, arguments: Value| {
            self.effective_call(original, arguments)
        };
        apply(ProgramContext::new(
            self.tools.program_descriptors(),
            self.tools.programmatic_settings(),
            self.turn_id,
            self.message_count,
            &resolve_operation,
            &post_hook_binding_ids,
            &effective_call,
        ))
    }
}

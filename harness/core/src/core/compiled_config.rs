use crate::core::capabilities::{self, HookBindingIndex};
use crate::core::config::{self, HarnessConfig};
use crate::core::error::CoreError;
use crate::core::features::compaction::prompt_message;
use crate::core::features::skills;
use crate::core::model_input_budget::estimate_model_input_tokens;
use crate::core::prompt::build_system_prompt;
use crate::core::tools::resolved::ResolvedTools;
use crate::core::wire::message::{Message, StoredMessage};

/// A valid configuration with the tool catalog and hook index compiled from it.
///
/// Only [`CompiledConfig::compile`] builds one, so the configuration has passed
/// validation and the three always describe the same configuration.
#[derive(Debug, PartialEq)]
pub(crate) struct CompiledConfig {
    config: HarnessConfig,
    resolved_tools: ResolvedTools,
    hook_binding_index: HookBindingIndex,
}

impl CompiledConfig {
    pub(crate) fn compile(config: HarnessConfig) -> Result<Self, CoreError> {
        config::validate_settings(&config.settings)?;
        config::validate_skill_catalog_fingerprint(config.skill_catalog_fingerprint.as_deref())?;
        let resolved_tools = ResolvedTools::compile(&config)?;
        let hook_binding_index = HookBindingIndex::from_config(&config);
        let compiled = Self {
            config,
            resolved_tools,
            hook_binding_index,
        };
        compiled.validate_compaction_budget()?;
        skills::validate(compiled.config.skills())
            .map_err(|detail| CoreError::invalid_configuration("skills", detail))?;
        capabilities::validate(&compiled.config)
            .map_err(|detail| CoreError::invalid_configuration("capabilities", detail))?;
        Ok(compiled)
    }

    fn validate_compaction_budget(&self) -> Result<(), CoreError> {
        let config = &self.config;
        let Some(token_threshold) = config.settings.context.compaction_threshold() else {
            return Ok(());
        };
        let messages = vec![
            StoredMessage::generated_system(Message::system_text(build_system_prompt(
                config,
                self.resolved_tools.tool_group_inventory_prompt_section(),
            ))),
            prompt_message(""),
        ];
        if estimate_model_input_tokens(
            &messages,
            self.resolved_tools.top_level_tools(),
            config
                .settings
                .context
                .effective_image_delivery()
                .compaction,
            config.settings.context.model_facts.image_cost,
        )? >= token_threshold
        {
            return Err(CoreError::invalid_configuration(
                "settings.context.model_facts.compaction_threshold",
                "token threshold cannot fit the generated system prompt, tool catalog, and compaction prompt",
            ));
        }
        Ok(())
    }

    pub(crate) fn config(&self) -> &HarnessConfig {
        &self.config
    }

    pub(crate) fn resolved_tools(&self) -> &ResolvedTools {
        &self.resolved_tools
    }

    pub(crate) fn hook_binding_index(&self) -> &HookBindingIndex {
        &self.hook_binding_index
    }
}

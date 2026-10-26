use crate::core::capabilities;
use crate::core::config::{HarnessConfig, HarnessConfigUpdate};
use crate::core::features::background_processes;
use crate::core::features::skills;
use crate::core::prompt::PromptSettings;

// Pieces of the default guidance in `default_prompts.json`, combined per enabled feature.
const SKILLS_IDENTITY_GUIDANCE: &str = "Find relevant skills with the `skill` tool";
const SKILLS_AUTONOMY_ACTIVITY: &str = "loading skills";
const PROGRAMMATIC_IDENTITY_GUIDANCE: &str =
    "discover additional tools with `search_tool_functions`, and call them using `run_typescript`";
const PROGRAMMATIC_AUTONOMY_ACTIVITY: &str = "discovering tools";

pub(crate) fn configuration_update_changes_system_prompt(
    current: &HarnessConfig,
    update: &HarnessConfigUpdate,
) -> bool {
    update.system_instructions.is_some()
        || update.capabilities.is_some()
        || update.plugins.is_some()
        || update.settings.as_ref().is_some_and(|settings| {
            current.settings.prompts.guidance != settings.prompts.guidance
                || current.settings.prompts.programs != settings.prompts.programs
                || current.settings.prompts.agent_tools != settings.prompts.agent_tools
                || current
                    .settings
                    .tools
                    .command_environment
                    .changes_system_prompt(settings.tools.command_environment)
                || current
                    .settings
                    .tools
                    .background_processes
                    .changes_system_prompt(settings.tools.background_processes)
                || current
                    .settings
                    .tools
                    .subagents
                    .changes_system_prompt(&settings.tools.subagents)
        })
}

pub(crate) fn build_system_prompt(
    config: &HarnessConfig,
    tool_group_inventory_prompt_section: &str,
) -> String {
    let mut sections = vec![build_generic_system_prompt(config)];
    let system_instructions = config.system_instructions.trim();
    if !system_instructions.is_empty() {
        sections.push(format!("## Use-case instructions\n\n{system_instructions}"));
    }
    if !tool_group_inventory_prompt_section.is_empty() {
        sections.push(tool_group_inventory_prompt_section.to_string());
    }
    if let Some(section) = skills::catalog_prompt_section(config.skills()) {
        sections.push(section);
    }
    sections.extend(capabilities::render_sections(config));
    sections.join("\n\n")
}

fn build_generic_system_prompt(config: &HarnessConfig) -> String {
    let prompts = &config.settings.prompts;
    let programmatic = config.settings.tools.programmatic.is_enabled();
    let skill_mode = config.settings.tools.skills;
    // Name the `skill` tool only when the Session exposes it.
    let skills_on = skill_mode.exposes(config.skills());
    // The default guidance names the skill and programmatic tools, so it is rebuilt for the
    // features this Session has. An override is prompt research and is used as written.
    let (intro, autonomy) = if prompts.guidance == PromptSettings::default().guidance {
        (
            build_intro_prompt(skills_on, programmatic),
            build_autonomy_prompt(skills_on, programmatic),
        )
    } else {
        (
            prompts.guidance.intro.clone(),
            prompts.guidance.autonomy.clone(),
        )
    };
    // An empty intro or autonomy section is suppressed rather than left as an empty paragraph.
    let mut sections: Vec<String> = [intro, autonomy]
        .into_iter()
        .filter(|section| !section.trim().is_empty())
        .collect();
    if programmatic {
        sections.push(prompts.programs.tool_use.clone());
    }
    if config.settings.tools.background_processes.is_enabled() {
        let command_profile = config
            .settings
            .tools
            .command_environment
            .profile()
            .expect("validated background processes have a command environment");
        sections.push(background_processes::prompt_section(command_profile));
    }
    if config.settings.tools.subagents.is_enabled() {
        sections.push(config.settings.prompts.agent_tools.guidance.clone());
    }
    if skill_mode.exposes(config.skills()) {
        sections.push(skills::rules_prompt_section().to_string());
    }
    if programmatic {
        sections.push(prompts.programs.current_time.clone());
    }
    sections.join("\n\n")
}

fn build_intro_prompt(skills: bool, programmatic: bool) -> String {
    let intro = "You help users get complex tasks done. Act directly and proactively.";
    match (skills, programmatic) {
        (true, true) => format!(
            "{intro} {}, {}.",
            SKILLS_IDENTITY_GUIDANCE, PROGRAMMATIC_IDENTITY_GUIDANCE,
        ),
        (true, false) => format!("{intro} {}.", SKILLS_IDENTITY_GUIDANCE),
        (false, true) => format!("{intro} {}.", capitalize(PROGRAMMATIC_IDENTITY_GUIDANCE)),
        (false, false) => intro.to_string(),
    }
}

fn capitalize(text: &str) -> String {
    let mut characters = text.chars();
    characters
        .next()
        .map(|first| first.to_uppercase().chain(characters).collect())
        .unwrap_or_default()
}

fn build_autonomy_prompt(skills: bool, programmatic: bool) -> String {
    let activities = [
        skills.then_some(SKILLS_AUTONOMY_ACTIVITY),
        programmatic.then_some(PROGRAMMATIC_AUTONOMY_ACTIVITY),
    ]
    .into_iter()
    .flatten()
    .map(|activity| format!("{activity}, "))
    .collect::<String>();
    format!(
        r#"## Autonomy and initiative

- Treat the user's request as authorization to perform the normal, in-scope work needed to complete it. Start working without asking whether to proceed.
- Do not ask for confirmation before {activities}reading or searching available context, or performing other reversible investigation.
- When a tool call needs approval, make the tool call and let the runtime's approval mechanism handle it. Do not preemptively ask for permission in chat.
- When details are ambiguous, first use the available context and tools to resolve them. Ask a clarifying question only when missing information would materially change the result, cannot be discovered, or additional authorization is genuinely required.
- Make reasonable implementation choices, keep momentum, and continue until the requested outcome is complete or a real blocker remains."#
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::capabilities::{KnowledgeFolderAccess, KnowledgeFolderDefinition};
    use crate::core::testing::*;
    use crate::core::tools::resolved::ResolvedTools;

    #[test]
    fn guidance_with_every_feature_is_the_default_guidance() {
        let defaults = PromptSettings::default().guidance;

        assert_eq!(build_intro_prompt(true, true), defaults.intro);
        assert_eq!(build_autonomy_prompt(true, true), defaults.autonomy);
    }
    #[test]
    fn system_prompt_indexes_generic_tool_groups_and_skills() {
        let mut config = config();
        config.system_instructions = "Product instructions before dynamic catalogs.".to_string();
        config.capabilities.tool_groups.push(ToolGroupDefinition {
            name: "calendar".to_string(),
            description: "Read and update calendars.".to_string(),
            metadata: None,
            icon_url: None,
            tools: vec![ProvidedToolDefinition {
                name: "list_events".to_string(),
                description: "List events.".to_string(),
                input_schema: json!({"type": "object"}),
                output_schema: None,
                exposure: ProvidedToolExposure::Programmatic,
            }],
        });
        config.capabilities.skills.push(SkillDefinition {
            name: "review".to_string(),
            description: "Review <code> safely.".to_string(),
            path: "/skills/review<&/SKILL.md".to_string(),
        });
        config
            .capabilities
            .knowledge_folders
            .push(KnowledgeFolderDefinition {
                name: "project".to_string(),
                description: "Project context.".to_string(),
                path: "/knowledge/project/KNOWLEDGE.md".to_string(),
                access: KnowledgeFolderAccess::ReadWrite,
            });

        let step = advance(
            initial_state(config).unwrap(),
            user_message("work", UserMessageMode::Queue),
        )
        .unwrap();

        let system_prompt = message_text(&step.state.context.messages()[0]);
        assert!(system_prompt.contains("- calendar"));
        assert!(system_prompt.contains("- self"));
        assert!(!system_prompt.contains("Read and update calendars."));
        assert!(!system_prompt.contains("Coordinate bounded waits"));
        assert!(!system_prompt.contains("`tools.self.sleep({ seconds })`"));
        assert!(system_prompt.contains("<name>review</name>"));
        assert!(system_prompt.contains("<description>Review &lt;code&gt; safely.</description>"));
        assert!(system_prompt.contains("<path>/skills/review&lt;&amp;/SKILL.md</path>"));
        assert!(system_prompt.contains("<name>project</name>"));
        assert!(system_prompt.contains("`tools.agents"));
        let tool_use = system_prompt
            .find("## Using tool functions via run_typescript")
            .expect("tool-use guidance");
        let current_time = system_prompt.find("## Current time").expect("current time");
        let product_instructions = system_prompt
            .find("Product instructions before dynamic catalogs.")
            .expect("product instructions");
        let connector_inventory = system_prompt
            .find("### Searchable connector groups")
            .expect("connector inventory");
        let skill_catalog = system_prompt
            .rfind("<available-skills>")
            .expect("skill catalog");
        let knowledge_folders = system_prompt
            .find("## Available knowledge folders")
            .expect("knowledge folders");
        assert!(tool_use < current_time);
        assert!(current_time < product_instructions);
        assert!(product_instructions < connector_inventory);
        assert!(connector_inventory < skill_catalog);
        assert!(skill_catalog < knowledge_folders);
        assert!(system_prompt.contains("<available-knowledge-folders>"));
        assert!(system_prompt.contains("</available-knowledge-folders>"));
        assert!(
            system_prompt.contains(
                "## Use-case instructions\n\nProduct instructions before dynamic catalogs."
            )
        );
        let Some(Action::Completion {
            model_input:
                ModelInputUpdate {
                    tool_catalog: ModelToolCatalogUpdate::Replace { tools, .. },
                    ..
                },
            ..
        }) = step.effect
        else {
            panic!("expected a completion effect");
        };
        assert_eq!(
            tools.into_iter().map(|tool| tool.name).collect::<Vec<_>>(),
            [
                "search_tool_functions",
                "run_typescript",
                "read_file",
                "write_file",
                "edit",
                "bash",
                "skill",
            ]
        );
    }

    #[test]
    fn system_prompt_keeps_generic_intro_autonomy_and_time_guidance() {
        let mut config = config();
        config.capabilities.skills.push(SkillDefinition {
            name: "research".to_string(),
            description: "Research unfamiliar topics.".to_string(),
            path: "/skills/research/SKILL.md".to_string(),
        });

        let resolved_tools = ResolvedTools::compile(&config).expect("tools resolve");
        let prompt = build_system_prompt(
            &config,
            resolved_tools.tool_group_inventory_prompt_section(),
        );

        assert!(prompt.starts_with("You help users get complex tasks done."));
        assert!(prompt.contains("discover additional tools with `search_tool_functions`"));
        assert!(!prompt.contains("You are an expert agent"));
        assert!(prompt.contains("## Autonomy and initiative"));
        assert!(prompt.contains("Do not ask for confirmation before loading skills"));
        assert!(prompt.contains("let the runtime's approval mechanism handle it"));
        assert!(!prompt.contains("## Process"));
        assert!(!prompt.contains("Complete the task using tools as needed."));
        assert!(prompt.contains("call `run_typescript` to read `new Date().toISOString()`"));
        assert!(!prompt.contains("step()"));
        assert!(!prompt.contains("await step"));
        assert!(prompt.contains("proactively call the `skill` tool"));
        assert!(prompt.contains(
            "Do not read a root `SKILL.md` path with `read_file` after loading a skill."
        ));

        assert!(prompt.contains(
            "For unfamiliar tool calls, use this sequence: `best_match` → `details` → `run_typescript`."
        ));
        assert!(!prompt.contains("mode: \"best_match\""));
        assert!(!prompt.contains("all_connector_capabilities"));
        assert!(!prompt.contains("moreCandidatesAvailable"));
    }

    #[test]
    fn system_prompt_without_skills_names_no_skill_tool() {
        for mode in [skills::Mode::WhenConfigured, skills::Mode::Enabled] {
            let mut config = config();
            config.settings.tools.skills = mode;

            let resolved_tools = ResolvedTools::compile(&config).expect("tools resolve");
            let prompt = build_system_prompt(
                &config,
                resolved_tools.tool_group_inventory_prompt_section(),
            );

            assert!(
                resolved_tools
                    .top_level_tools()
                    .iter()
                    .all(|tool| tool.name != "skill"),
                "{mode:?}"
            );
            assert!(!prompt.contains("`skill`"), "{mode:?}");
            assert!(!prompt.contains("## Available Skills"), "{mode:?}");
        }
    }

    #[test]
    fn empty_intro_and_autonomy_leave_no_empty_paragraphs() {
        let mut config = config();
        config.settings.prompts.guidance.intro = String::new();
        config.settings.prompts.guidance.autonomy = " \n".to_string();

        let resolved_tools = ResolvedTools::compile(&config).expect("tools resolve");
        let prompt = build_system_prompt(
            &config,
            resolved_tools.tool_group_inventory_prompt_section(),
        );

        assert!(prompt.starts_with(&config.settings.prompts.programs.tool_use));
        assert!(!prompt.contains("You help users get complex tasks done."));
        assert!(!prompt.contains("## Autonomy and initiative"));
        assert!(!prompt.contains("\n\n\n"));
    }

    #[test]
    fn product_instructions_stay_in_the_common_prefix_across_user_catalogs() {
        let prompt_for = |connector_name: &str, skill_name: &str| {
            let mut config = config();
            config.system_instructions = "Stable product instructions.".to_string();
            config.capabilities.tool_groups.push(ToolGroupDefinition {
                name: connector_name.to_string(),
                description: "User-specific connector.".to_string(),
                metadata: None,
                icon_url: None,
                tools: vec![ProvidedToolDefinition {
                    name: "search".to_string(),
                    description: "Search records.".to_string(),
                    input_schema: json!({"type": "object"}),
                    output_schema: None,
                    exposure: ProvidedToolExposure::Programmatic,
                }],
            });
            config.capabilities.skills.push(SkillDefinition {
                name: skill_name.to_string(),
                description: "User-specific skill.".to_string(),
                path: format!("/skills/{skill_name}/SKILL.md"),
            });
            let resolved_tools = ResolvedTools::compile(&config).expect("tools resolve");
            build_system_prompt(
                &config,
                resolved_tools.tool_group_inventory_prompt_section(),
            )
        };

        let first = prompt_for("calendar", "review");
        let second = prompt_for("mail", "research");
        let product_prefix_end = first
            .find("Stable product instructions.")
            .expect("product instructions")
            + "Stable product instructions.".len();

        assert_eq!(&first[..product_prefix_end], &second[..product_prefix_end]);
        assert_ne!(&first[product_prefix_end..], &second[product_prefix_end..]);
    }

    #[test]
    fn sleep_guidance_is_only_rendered_with_background_processes() {
        let mut without_background = config();
        let without_tools = ResolvedTools::compile(&without_background).expect("tools resolve");
        let without_prompt = build_system_prompt(
            &without_background,
            without_tools.tool_group_inventory_prompt_section(),
        );

        without_background.settings.tools.background_processes = BackgroundProcessMode::Enabled;
        let with_tools = ResolvedTools::compile(&without_background).expect("tools resolve");
        let with_prompt = build_system_prompt(
            &without_background,
            with_tools.tool_group_inventory_prompt_section(),
        );

        assert!(!without_prompt.contains("tools.self.sleep"));
        assert!(with_prompt.contains("## Background processes"));
        assert!(with_prompt.contains("tools.self.sleep"));
    }
}

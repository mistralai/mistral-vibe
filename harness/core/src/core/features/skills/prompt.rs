use super::SkillDefinition;

pub(crate) fn rules_prompt_section() -> &'static str {
    r#"## Available Skills

Skills contain task-specific instructions. When a request matches a skill description, proactively call the `skill` tool with its exact name before starting the task, then follow the loaded instructions. If that skill's full instructions are already present in the current context, follow them without calling `skill` again. Load every skill that materially applies.

Call `skill` only with a name listed in `<available-skills>`. Connector names and unlisted names in skill instructions are not skills.

Load a skill only with `skill`, never by reading its root `SKILL.md` path with `read_file`. A listed skill's files may not exist until `skill` loads it. After loading, use `read_file` only for support or reference files mentioned by the loaded skill."#
}

pub(crate) fn catalog_prompt_section<'a>(
    skills: impl IntoIterator<Item = &'a SkillDefinition>,
) -> Option<String> {
    let entries = skills
        .into_iter()
        .map(|skill| {
            format!(
                r#"  <skill>
    <name>{}</name>
    <description>{}</description>
  </skill>"#,
                escape_xml(&skill.name),
                escape_xml(&skill.description)
            )
        })
        .collect::<Vec<_>>();
    if entries.is_empty() {
        return None;
    }
    Some(format!(
        r#"<available-skills>
{}
</available-skills>"#,
        entries.join("\n")
    ))
}

fn escape_xml(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

use super::SkillDefinition;

pub(crate) const IDENTITY_GUIDANCE: &str = "Find relevant skills with the `skill` tool";
pub(crate) const AUTONOMY_ACTIVITY: &str = "loading skills";

pub(crate) fn rules_prompt_section() -> &'static str {
    r#"## Available Skills

Skills contain task-specific instructions. When a request matches a skill description, proactively call the `skill` tool with its exact name before starting the task, then follow the loaded instructions. Load every skill that materially applies.

Call `skill` only with a name listed in `<available-skills>`. Connector names and unlisted names in skill instructions are not skills.

Do not read a root `SKILL.md` path with `read_file` after loading a skill. Use `read_file` only for support or reference files mentioned by the loaded skill."#
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
    <path>{}</path>
  </skill>"#,
                escape_xml(&skill.name),
                escape_xml(&skill.description),
                escape_xml(&skill.path)
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

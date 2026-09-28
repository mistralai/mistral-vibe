//! Repository matching and ranked project-picker rows.

use caseless::default_case_fold_str as casefold;

use crate::server::proto_projects::{PickerContext, PickerView, Project};

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Item {
    Project { index: usize, rank: u8 },
    LoadMore,
    Create { name: String, recommended: bool },
    Unlink,
    Section(&'static str),
}

impl Item {
    pub fn option_id(&self, view: &PickerView) -> Option<String> {
        Some(match self {
            Self::Project { index, .. } => {
                format!("project:{}", view.state.projects[*index].project_id)
            }
            Self::LoadMore => "action:load_more".into(),
            Self::Create { .. } => "action:create".into(),
            Self::Unlink => "action:unlink".into(),
            Self::Section(_) => return None,
        })
    }

    pub fn selectable(&self) -> bool {
        !matches!(self, Self::Section(_))
    }

    pub fn label<'a>(&'a self, view: &'a PickerView) -> &'a str {
        match self {
            Self::Project { index, .. } => &view.state.projects[*index].name,
            Self::LoadMore => "Load more projects...",
            Self::Create { .. } => "Create new project",
            Self::Unlink => "Unlink project",
            Self::Section(label) => label,
        }
    }
}

pub fn build_project_picker_items(view: &PickerView, query: &str) -> Vec<Item> {
    let context = &view.context;
    let query_folded = casefold(query.trim());
    let repo = normalize_repo_url(&context.repo_url);
    let mut projects: Vec<_> = view
        .state
        .projects
        .iter()
        .enumerate()
        .filter(|(_, project)| {
            !project.is_read_only
                && project
                    .repositories
                    .iter()
                    .any(|r| normalize_repo_url(&r.repo_url) == repo)
                && (casefold(&project.name).contains(&query_folded)
                    || project
                        .repositories
                        .iter()
                        .any(|r| casefold(&r.repo_url).contains(&query_folded)))
        })
        .map(|(index, project)| (rank(context, project), casefold(&project.name), index))
        .collect();
    projects.sort_by(|a, b| (a.0, &a.1).cmp(&(b.0, &b.1)));
    let has_more = view.state.next_cursor.is_some();
    let recommended = projects.is_empty() && !has_more;
    let mut items = Vec::new();
    if !projects.is_empty() || has_more {
        items.push(Item::Section("Projects"));
    }
    items.extend(
        projects
            .into_iter()
            .map(|(rank, _, index)| Item::Project { index, rank }),
    );
    if has_more {
        items.push(Item::LoadMore);
    }
    items.extend([
        Item::Section(" "),
        Item::Section(" "),
        Item::Section("Actions"),
    ]);
    let name = if query.trim().is_empty() {
        suggested_project_name(context)
    } else {
        query.trim().into()
    };
    items.push(Item::Create { name, recommended });
    if context.saved_link.is_some() {
        items.push(Item::Unlink);
    }
    items
}

fn rank(context: &PickerContext, project: &Project) -> u8 {
    if context.saved_link.as_ref().is_some_and(|link| {
        link.project_id == project.project_id
            && normalize_repo_url(&link.repo_url) == normalize_repo_url(&context.repo_url)
    }) {
        0
    } else if project.repositories.len() == 1 {
        1
    } else {
        2
    }
}

fn suggested_project_name(context: &PickerContext) -> String {
    if !context.repo_name.trim().is_empty() {
        return context.repo_name.trim().into();
    }
    normalize_repo_url(&context.repo_url)
        .rsplit('/')
        .next()
        .filter(|name| !name.is_empty())
        .unwrap_or("vibe-project")
        .into()
}

pub fn normalize_repo_url(value: &str) -> String {
    let value = value.trim().trim_end_matches('/');
    let value = if let Some(path) = value.strip_prefix("git@github.com:") {
        format!("github.com/{path}")
    } else {
        url_path(value)
    };
    without_git_suffix(&value).to_lowercase()
}

pub fn repo_url_label(value: &str) -> String {
    let value = value.trim().trim_end_matches('/');
    let ssh = value
        .strip_prefix("ssh://")
        .unwrap_or(value)
        .strip_prefix("git@");
    let value = match ssh.and_then(|s| s.split_once([':', '/'])) {
        Some((host, path)) => format!("{host}/{path}"),
        None => url_path(value),
    };
    without_git_suffix(&value).into()
}

fn url_path(value: &str) -> String {
    if let Some(rest) = value
        .strip_prefix("//")
        .or_else(|| value.split_once("://").map(|(_, rest)| rest))
    {
        if let Some((host, path)) = rest.split_once('/') {
            let path = path.split(['?', '#']).next().unwrap_or("");
            return format!("{host}/{}", path.trim_start_matches('/'));
        }
    }
    value.into()
}

fn without_git_suffix(value: &str) -> &str {
    let value = value.trim_end_matches('/');
    value.strip_suffix(".git").unwrap_or(value)
}

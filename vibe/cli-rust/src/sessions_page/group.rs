//! Which project a session belongs to: its project stamp, else its repository, else its folder.

use std::collections::HashMap;
use std::path::Path;

use super::row::{Project, Session};

/// A group of the list: a stable key and the label its header shows.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct Group {
    pub key: String,
    pub label: String,
}

/// One row of the folders column: a group and the sessions it lists.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Folder {
    pub key: String,
    pub label: String,
    pub sessions: Vec<usize>,
}

/// The project group of `session`.
pub fn of(session: &Session, projects: &[Project]) -> Group {
    if let Some(project) = stamped(session, projects) {
        return named(project);
    }
    let folder = session
        .repository_root
        .as_deref()
        .or(session.cwd.as_deref());
    match folder {
        Some(folder) => of_folder(folder, projects),
        None => Group {
            key: String::new(),
            label: "No folder".into(),
        },
    }
}

/// The group of a folder no session row describes, such as the current one.
pub fn of_folder(folder: &str, projects: &[Project]) -> Group {
    if let Some(project) = projects
        .iter()
        .find(|project| project.root_paths.iter().any(|root| root == folder))
    {
        return named(project);
    }
    Group {
        key: folder.to_owned(),
        label: folder_name(folder),
    }
}

fn stamped<'a>(session: &Session, projects: &'a [Project]) -> Option<&'a Project> {
    let stamp = session.project.as_ref()?;
    let id = stamp.id.as_deref()?;
    projects.iter().find(|project| match stamp.kind.as_str() {
        "local" => project.id == id,
        "cloud" => {
            project
                .cloud
                .as_ref()
                .and_then(|cloud| cloud.project_id.as_deref())
                == Some(id)
        }
        _ => false,
    })
}

fn named(project: &Project) -> Group {
    let label = match project.name.trim() {
        "" => project
            .root_paths
            .first()
            .map_or_else(|| "Project".to_owned(), |root| folder_name(root)),
        name => name.to_owned(),
    };
    Group {
        key: format!("project:{}", project.id),
        label,
    }
}

/// Folder groups that share a label show just enough of their home-collapsed
/// path to tell them apart (`~/…/feature/vibe` beside `~/…/main/vibe`).
pub fn distinguish(groups: &mut [&mut Group]) {
    let mut by_label: HashMap<String, Vec<usize>> = HashMap::new();
    for (index, group) in groups.iter().enumerate() {
        by_label.entry(group.label.clone()).or_default().push(index);
    }
    for indices in by_label.into_values().filter(|indices| indices.len() > 1) {
        let paths: Vec<Vec<String>> = indices
            .iter()
            .map(|&index| components(&groups[index].key))
            .collect();
        for (slot, &index) in indices.iter().enumerate() {
            if is_folder(groups[index]) {
                groups[index].label = shortest_unique(&paths, slot);
            }
        }
    }
}

fn is_folder(group: &Group) -> bool {
    !group.key.is_empty() && !group.key.starts_with("project:")
}

fn components(folder: &str) -> Vec<String> {
    crate::utils::paths::collapse_home(folder)
        .split(['/', '\\'])
        .map(str::to_owned)
        .collect()
}

/// The shortest tail of `paths[slot]` no other path ends with, under its first component.
fn shortest_unique(paths: &[Vec<String>], slot: usize) -> String {
    let own = &paths[slot];
    let unique = |len: usize| {
        let tail = &own[own.len() - len..];
        paths
            .iter()
            .enumerate()
            .all(|(other, path)| other == slot || !path.ends_with(tail))
    };
    let len = (1..own.len()).find(|&len| unique(len));
    match len {
        Some(len) if len + 1 < own.len() => {
            format!("{}/…/{}", own[0], own[own.len() - len..].join("/"))
        }
        _ => own.join("/"),
    }
}

fn folder_name(folder: &str) -> String {
    Path::new(folder).file_name().map_or_else(
        || folder.to_owned(),
        |name| name.to_string_lossy().into_owned(),
    )
}

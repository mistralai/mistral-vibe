//! The page's list as lines: sections, started-session families, filters and sort.

use std::collections::{HashMap, HashSet};

use super::group::{self, Folder, Group};
use super::row::{Project, Session};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Sort {
    /// Last turn first, like Vibe Desktop.
    #[default]
    Activity,
    Created,
}

/// Where a started session sits under its starter.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Branch {
    Middle,
    Last,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Line {
    Header {
        label: String,
        count: usize,
    },
    /// `option` is the row's position among the selectable rows.
    Row {
        option: usize,
        session: usize,
        branch: Option<Branch>,
        folded: usize,
    },
    Blank,
}

/// What the list shows, beyond the sessions themselves.
pub struct Query<'a> {
    pub sort: Sort,
    pub search: &'a str,
    pub folder: Option<&'a str>,
    pub archived: bool,
    /// Sessions whose folder is gone from disk are listed too.
    pub gone: bool,
    pub current_id: Option<&'a str>,
    pub current_group: Option<Group>,
    /// Families whose fold differs from the default (folded unless they hold the current session).
    pub toggled: &'a HashSet<String>,
    /// The folders column shows: the list is flat, holding the `group` folder's sessions, or all.
    pub flat: bool,
    pub group: Option<&'a str>,
}

/// The laid-out list: its lines, and the session index of each selectable row.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct View {
    pub lines: Vec<Line>,
    pub rows: Vec<usize>,
    /// The family (top starter id) of each selectable row.
    pub families: Vec<String>,
    /// Every group the filters leave, in the list's order.
    pub folders: Vec<Folder>,
    /// The position in `folders` of the folder the flat list shows; `None` for all.
    pub folder: Option<usize>,
}

struct Family {
    top: usize,
    members: Vec<usize>,
}

pub fn build(sessions: &[Session], projects: &[Project], query: &Query) -> View {
    let visible: Vec<usize> = (0..sessions.len())
        .filter(|&index| shown(&sessions[index], projects, query))
        .collect();
    let mut sections: Vec<(Group, Vec<Family>)> = Vec::new();
    for family in families(sessions, &visible, query.sort) {
        let section = group::of(&sessions[family.top], projects);
        match sections.iter_mut().find(|(group, _)| *group == section) {
            Some((_, families)) => families.push(family),
            None => sections.push((section, vec![family])),
        }
    }
    order_sections(&mut sections, sessions, query);
    group::distinguish(
        &mut sections
            .iter_mut()
            .map(|(group, _)| group)
            .collect::<Vec<_>>(),
    );
    let mut view = View {
        folders: sections
            .iter()
            .map(|(group, families)| Folder {
                key: group.key.clone(),
                label: group.label.clone(),
                sessions: families.iter().flat_map(|f| f.members.clone()).collect(),
            })
            .collect(),
        ..View::default()
    };
    if query.flat {
        view.folder = query
            .group
            .and_then(|key| view.folders.iter().position(|folder| folder.key == key));
        let mut flat: Vec<Family> = sections
            .into_iter()
            .enumerate()
            .filter(|(index, _)| view.folder.is_none_or(|folder| folder == *index))
            .flat_map(|(_, (_, families))| families)
            .collect();
        flat.sort_by_key(|family| std::cmp::Reverse(family_rank(sessions, family, query.sort)));
        for family in &flat {
            push_family(&mut view, sessions, family, query);
        }
        return view;
    }
    for (group, families) in sections {
        let count = families.iter().map(|family| family.members.len()).sum();
        view.lines.push(Line::Header {
            label: group.label,
            count,
        });
        for family in &families {
            push_family(&mut view, sessions, family, query);
        }
        view.lines.push(Line::Blank);
    }
    view
}

fn shown(session: &Session, projects: &[Project], query: &Query) -> bool {
    if session.archived_at.is_some() != query.archived {
        return false;
    }
    if session.cwd_missing && !query.gone {
        return false;
    }
    if query.folder.is_some() && session.cwd.as_deref() != query.folder {
        return false;
    }
    let needle = query.search.trim().to_lowercase();
    if needle.is_empty() {
        return true;
    }
    let label = group::of(session, projects).label;
    [
        session.title.as_str(),
        session.preview.as_str(),
        session.cwd.as_deref().unwrap_or_default(),
        label.as_str(),
    ]
    .iter()
    .any(|field| field.to_lowercase().contains(&needle))
}

/// Started sessions join their topmost listed starter, one level deep.
fn families(sessions: &[Session], visible: &[usize], sort: Sort) -> Vec<Family> {
    let by_id: HashMap<&str, usize> = visible
        .iter()
        .map(|&index| (sessions[index].id.as_str(), index))
        .collect();
    let mut families: Vec<Family> = Vec::new();
    let mut slots: HashMap<usize, usize> = HashMap::new();
    let mut ordered = visible.to_vec();
    ordered.sort_by(|&a, &b| rank(&sessions[b], sort).cmp(&rank(&sessions[a], sort)));
    for index in ordered {
        let top = top_starter(sessions, &by_id, index);
        let slot = *slots.entry(top).or_insert_with(|| {
            families.push(Family {
                top,
                members: vec![top],
            });
            families.len() - 1
        });
        if index != top {
            families[slot].members.push(index);
        }
    }
    families
}

fn top_starter(sessions: &[Session], by_id: &HashMap<&str, usize>, index: usize) -> usize {
    let mut top = index;
    let mut seen = HashSet::from([index]);
    while let Some(starter) = sessions[top]
        .started_by_session_id
        .as_deref()
        .and_then(|id| by_id.get(id))
    {
        if !seen.insert(*starter) {
            break;
        }
        top = *starter;
    }
    top
}

fn rank(session: &Session, sort: Sort) -> (u64, u64, &str) {
    match sort {
        Sort::Activity => (session.activity(), session.created_at, &session.id),
        Sort::Created => (session.created_at, 0, &session.id),
    }
}

/// A family ranks by its most recent member, under the active sort.
fn family_rank<'a>(sessions: &'a [Session], family: &Family, sort: Sort) -> (u64, u64, &'a str) {
    family
        .members
        .iter()
        .map(|&index| rank(&sessions[index], sort))
        .max()
        .unwrap_or_default()
}

/// The current project first, then the others by their most recent family.
fn order_sections(sections: &mut [(Group, Vec<Family>)], sessions: &[Session], query: &Query) {
    for (_, families) in sections.iter_mut() {
        families.sort_by(|a, b| {
            family_rank(sessions, b, query.sort).cmp(&family_rank(sessions, a, query.sort))
        });
    }
    sections.sort_by(|(a, families_a), (b, families_b)| {
        let place = |group: &Group| match Some(group) == query.current_group.as_ref() {
            true => 0,
            false => 1,
        };
        let best = |families: &[Family]| {
            families
                .iter()
                .map(|family| family_rank(sessions, family, query.sort))
                .max()
        };
        place(a)
            .cmp(&place(b))
            .then_with(|| best(families_b).cmp(&best(families_a)))
    });
}

fn push_family(view: &mut View, sessions: &[Session], family: &Family, query: &Query) {
    let top_id = sessions[family.top].id.clone();
    let holds_current = family
        .members
        .iter()
        .any(|&index| Some(sessions[index].id.as_str()) == query.current_id);
    let folded = !holds_current != query.toggled.contains(&top_id);
    let children = &family.members[1..];
    push_row(
        view,
        family.top,
        None,
        if folded { children.len() } else { 0 },
        &top_id,
    );
    if folded {
        return;
    }
    for (position, &child) in children.iter().enumerate() {
        let branch = match position + 1 == children.len() {
            true => Branch::Last,
            false => Branch::Middle,
        };
        push_row(view, child, Some(branch), 0, &top_id);
    }
}

fn push_row(view: &mut View, session: usize, branch: Option<Branch>, folded: usize, family: &str) {
    view.lines.push(Line::Row {
        option: view.rows.len(),
        session,
        branch,
        folded,
    });
    view.rows.push(session);
    view.families.push(family.to_owned());
}

impl View {
    /// The line map `OptionList::layout` takes: the option on each line.
    pub fn options(&self) -> Vec<Option<usize>> {
        self.lines
            .iter()
            .map(|line| match line {
                Line::Row { option, .. } => Some(*option),
                _ => None,
            })
            .collect()
    }
}

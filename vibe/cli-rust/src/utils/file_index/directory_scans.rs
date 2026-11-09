//! Bounded, order-independent roots for directory creation and rename walks.

use std::path::{Path, PathBuf};
use std::sync::atomic::AtomicBool;

use ignore::IncrementalIgnore;
use notify::event::CreateKind;
use notify::{event::ModifyKind, Event, EventKind};

use super::{is_git_path, stopped};

const MAX_ROOTS: usize = 128;

#[derive(Default)]
pub(super) struct DirectoryScans(Vec<(PathBuf, bool)>);

impl DirectoryScans {
    pub fn plan(
        root: &Path,
        events: &[Event],
        ignore: &mut IncrementalIgnore,
        stop: Option<&AtomicBool>,
    ) -> Self {
        let mut scans = Self::default();
        for event in events {
            if !matches!(
                event.kind,
                EventKind::Create(CreateKind::Any | CreateKind::Folder)
                    | EventKind::Modify(ModifyKind::Name(_))
            ) {
                continue;
            }
            for path in &event.paths {
                if stopped(stop) {
                    return scans;
                }
                let Ok(relative) = path.strip_prefix(root) else {
                    continue;
                };
                if is_git_path(relative)
                    || !path.is_dir()
                    || ignore.matched(relative, true).is_ignore()
                {
                    continue;
                }
                if scans.0.iter().any(|(parent, _)| path.starts_with(parent)) {
                    continue;
                }
                scans.0.retain(|(child, _)| !child.starts_with(path));
                // Extra independent roots keep the existing per-event walk instead of forcing a rebuild.
                if scans.0.len() < MAX_ROOTS {
                    scans.0.push((path.clone(), false));
                }
            }
        }
        scans
    }

    pub fn take(&mut self, path: &Path) -> Option<PathBuf> {
        let Some((root, scanned)) = self.0.iter_mut().find(|(root, _)| path.starts_with(root))
        else {
            return Some(path.to_owned());
        };
        if *scanned {
            return None;
        }
        *scanned = true;
        Some(root.clone())
    }
}

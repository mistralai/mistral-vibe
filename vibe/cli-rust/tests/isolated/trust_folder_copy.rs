//! Trust copying preserves source whitespace and Unicode, not visual wrapping.

use ratatui::layout::Rect;

use vibe_rs::app::{App, Selection};
use vibe_rs::selection::{region, RegionId, ScrollTarget};
use vibe_rs::server::WorkspaceTrustDetails;

fn selected_document(width: u16, target: ScrollTarget, reverse: bool) -> App {
    let mut app = App::default();
    app.trust.details = Some(WorkspaceTrustDetails {
        cwd: "/home/user/a long folder/with spaces/and more directories/workspace".to_owned(),
        repo_root: None,
        detected_files: vec![
            "folder with   spaces/a-long-file-name.md".to_owned(),
            "école/文件/e\u{301}tudes/another-long-file.md".to_owned(),
            "first line\nsecond line".to_owned(),
        ],
        repo_detected_files: Vec::new(),
        repo_explicitly_untrusted: false,
        settings_path:
            "/home/user/config with spaces/a-very-long-settings-directory/trusted_folders.toml"
                .to_owned(),
        available_decisions: vec!["trust_cwd".to_owned(), "decline".to_owned()],
    });
    let left = 20;
    app.view.selection_region.area = Rect::new(left, 5, width, 100);
    app.view.selection_region.end_exclusive = true;
    let start = (left, 0);
    let end = (left + width - 1, 1000);
    app.selection.region = Some(Selection {
        owner: RegionId::Main,
        anchor: if reverse { end } else { start },
        head: if reverse { start } else { end },
        pending_copy: false,
        edge_scroll: 0,
        scroll_target: target,
        table_cell: None,
        text: String::new(),
    });
    app
}

#[test]
fn offscreen_files_keep_source_spaces_unicode_and_hard_newlines() {
    for width in [8, 17, 47, 59] {
        for reverse in [false, true] {
            let app = selected_document(width, ScrollTarget::Trust, reverse);
            let files = &app.trust.details.as_ref().unwrap().detected_files;
            let expected = files
                .iter()
                .map(|file| format!("\u{2022} {file}"))
                .collect::<Vec<_>>()
                .join("\n");

            let copied = region::extract_document(&app).unwrap();

            assert!(copied.ends_with(&expected), "width {width}: {copied:?}");
            assert!(!copied.contains("\n\n"));
        }
    }
}

#[test]
fn fixed_paths_and_settings_keep_spaces_at_soft_wraps() {
    for width in [38, 48, 58] {
        for reverse in [false, true] {
            let app = selected_document(width, ScrollTarget::None, reverse);
            let details = app.trust.details.as_ref().unwrap();

            let copied = region::extract_document(&app).unwrap();

            assert!(copied.contains(&details.cwd), "width {width}: {copied:?}");
            assert!(copied.ends_with(&format!(
                "Setting will be saved in: {}",
                details.settings_path
            )));
        }
    }
}

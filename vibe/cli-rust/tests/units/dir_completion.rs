//! `/cd` folder completion lists subfolders only, hiding dot folders unless asked.

use vibe_rs::dir_completion::subfolders;

#[test]
fn lists_matching_subfolders_sorted() {
    let root = tempfile::tempdir().unwrap();
    for name in ["beta", "alpha", ".hidden", "bravo"] {
        std::fs::create_dir(root.path().join(name)).unwrap();
    }
    std::fs::write(root.path().join("file.md"), "").unwrap();
    assert_eq!(subfolders(root.path(), ""), ["alpha", "beta", "bravo"]);
    assert_eq!(subfolders(root.path(), "b"), ["beta", "bravo"]);
    assert_eq!(subfolders(root.path(), "."), [".hidden"]);
    assert!(subfolders(&root.path().join("missing"), "").is_empty());
}

#[test]
fn leaving_the_cd_completion_forgets_its_folder_read() {
    use vibe_rs::input_modes::InputMode;
    let mut app = vibe_rs::app::App::default();
    app.chat_input.mode = InputMode::Slash;
    app.chat_input.input = "cd ".into();
    app.chat_input.cursor = app.chat_input.input.len();
    vibe_rs::completion_manager::refresh(&mut app);
    assert!(
        app.completion.cd_dirs.holds_a_read(),
        "`/cd ` asks for its folder"
    );
    app.chat_input.input.clear();
    app.chat_input.cursor = 0;
    vibe_rs::completion_manager::refresh(&mut app);
    assert!(
        !app.completion.cd_dirs.holds_a_read(),
        "a folder created meanwhile shows the next time"
    );
}

/// The completion labels `input` lists once its folder read lands.
async fn listed(
    app: &mut vibe_rs::app::App,
    rx: &mut tokio::sync::mpsc::Receiver<vibe_rs::ui_event::UiEvent>,
    input: &str,
) -> Vec<String> {
    app.chat_input.input = input.into();
    app.chat_input.cursor = input.len();
    vibe_rs::completion_manager::refresh(app);
    let read = tokio::time::timeout(std::time::Duration::from_secs(5), rx.recv());
    if let Ok(Some(vibe_rs::ui_event::UiEvent::Dirs(event))) = read.await {
        vibe_rs::dir_completion::apply_event(app, event);
    }
    app.completion
        .entries
        .iter()
        .map(|entry| entry.label.clone())
        .collect()
}

/// A folder whose name has a space completes escaped, and completion goes on
/// inside it: `/cd My\ Folder/` lists `My Folder`'s subfolders.
#[tokio::test]
async fn a_folder_with_a_space_completes_escaped_and_lists_its_subfolders() {
    let root = tempfile::tempdir().unwrap();
    std::fs::create_dir_all(root.path().join("My Folder").join("inner")).unwrap();
    let mut app = vibe_rs::app::App::default();
    let (tx, mut rx) = tokio::sync::mpsc::channel(8);
    app.events = Some(tx);
    app.session.cwd = Some(root.path().to_string_lossy().into_owned());
    app.chat_input.mode = vibe_rs::input_modes::InputMode::Slash;

    assert_eq!(listed(&mut app, &mut rx, "cd My").await, [r"My\ Folder/"]);
    assert_eq!(
        listed(&mut app, &mut rx, r"cd My\ Folder/").await,
        [r"My\ Folder/inner/"]
    );
}

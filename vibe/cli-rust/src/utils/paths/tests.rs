use super::*;

#[test]
fn dangerous_directories_are_named_by_location() {
    let home = Path::new("/Users/tester");
    let reason = |path: &str| dangerous_directory_reason_at(Path::new(path), home);
    assert_eq!(
        reason("/Users/tester").as_deref(),
        Some("You are in the home directory")
    );
    assert_eq!(
        reason("/Users/tester/Downloads").as_deref(),
        Some("You are in the Downloads folder")
    );
    assert_eq!(
        reason("/private").as_deref(),
        Some("You are in the System private folder")
    );
    assert_eq!(
        reason("/usr").as_deref(),
        Some("You are in the System usr folder")
    );
}

#[test]
fn safe_directories_have_no_reason() {
    let home = Path::new("/Users/tester");
    assert_eq!(
        dangerous_directory_reason_at(Path::new("/Users/tester/projects"), home),
        None
    );
    assert_eq!(dangerous_directory_reason_at(Path::new("/tmp"), home), None);
    assert_eq!(
        dangerous_directory_reason_at(Path::new("/Users/other"), home),
        None
    );
}

//! A null `title`, `preview` or `updatedAt` still lists the session.

use serde_json::json;
use vibe_rs::resume_picker::Session;

#[test]
fn null_fields_keep_the_row() {
    let items = json!([
        {"id": "kept", "title": null, "preview": null, "updatedAt": null},
        {"preview": "no id"},
        {"id": "dated", "preview": "hello", "updatedAt": 5},
    ]);
    let sessions: Vec<Session> = vibe_rs::utils::json::lenient(&items).unwrap();
    assert_eq!(sessions.len(), 2);
    assert_eq!(sessions[0].id, "kept");
    assert_eq!(sessions[0].title, "");
    assert_eq!(sessions[0].preview, "");
    assert_eq!(sessions[0].updated_at, 0);
    assert_eq!(sessions[1].id, "dated");
    assert_eq!(sessions[1].preview, "hello");
    assert_eq!(sessions[1].updated_at, 5);
}

use super::*;

#[test]
fn schema_document_returns_the_schema_with_its_version() {
    let response = json!({
        "configSchemaVersion": "sha256:abc",
        "schema": {"type": "object", "properties": {}},
    });

    let schema = schema_document(&response).unwrap();

    assert_eq!(schema["type"], "object");
    assert_eq!(schema["$comment"], "version sha256:abc");
}

#[test]
fn schema_document_refuses_a_response_without_a_schema() {
    let error = schema_document(&json!({"configSchemaVersion": "x"})).unwrap_err();

    assert!(error.to_string().contains("missing schema"));
}

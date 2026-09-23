use std::io;

use serde::Serialize as _;
use serde_json::{
    Value,
    ser::{Formatter, Serializer},
};

use crate::core::tools::external::{ExternalTool, ExternalToolCall};

/// Tool arguments as exposed to permission rules.
///
/// Filter text uses recursively sorted object keys and Python-style JSON
/// separators so matching is deterministic and compatible with existing rules.
pub(super) struct PublicArguments<'call> {
    value: &'call Value,
}

impl<'call> PublicArguments<'call> {
    pub(super) fn from_call(call: &'call ExternalToolCall) -> Self {
        let value = match &call.call {
            ExternalTool::RuntimeBuiltin { arguments, .. }
            | ExternalTool::Provided { arguments, .. } => arguments,
        };

        Self { value }
    }

    pub(super) fn to_filter_text(&self) -> String {
        let mut value = self.value.to_owned();
        value.sort_all_objects();

        let mut output = Vec::new();
        value
            .serialize(&mut Serializer::with_formatter(
                &mut output,
                FilterTextFormatter,
            ))
            .expect("serializing JSON arguments cannot fail");
        String::from_utf8(output).expect("JSON serialization produces UTF-8")
    }
}

struct FilterTextFormatter;

impl Formatter for FilterTextFormatter {
    fn begin_array_value<W>(&mut self, writer: &mut W, first: bool) -> io::Result<()>
    where
        W: ?Sized + io::Write,
    {
        if !first {
            writer.write_all(b", ")?;
        }
        Ok(())
    }

    fn begin_object_key<W>(&mut self, writer: &mut W, first: bool) -> io::Result<()>
    where
        W: ?Sized + io::Write,
    {
        self.begin_array_value(writer, first)
    }

    fn begin_object_value<W>(&mut self, writer: &mut W) -> io::Result<()>
    where
        W: ?Sized + io::Write,
    {
        writer.write_all(b": ")
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn to_filter_text_sorts_object_keys_and_uses_stable_separators() {
        let value = json!({"z": 1, "a": {"d": 2, "c": 3}});
        let arguments = PublicArguments { value: &value };

        assert_eq!(
            arguments.to_filter_text(),
            r#"{"a": {"c": 3, "d": 2}, "z": 1}"#
        );
    }

    #[test]
    fn to_filter_text_serializes_arrays_and_scalar_values() {
        let value = json!([null, true, false, "line\n\"quoted\"", [2, 1]]);
        let arguments = PublicArguments { value: &value };

        assert_eq!(
            arguments.to_filter_text(),
            r#"[null, true, false, "line\n\"quoted\"", [2, 1]]"#
        );
    }
}

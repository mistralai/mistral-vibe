(type_declaration
  (type_specifier
    name: (type_identifier) @name.definition.type)) @definition.type

(function_declaration
  name: (identifier) @name.definition.function) @definition.function

(method_declaration
  name: (field_identifier) @name.definition.method) @definition.method

(call_expression
  function: [
    (identifier) @name.reference.call
    (selector_expression
      field: (field_identifier) @name.reference.call)
  ]) @reference.call

(import_spec
  name: (package_identifier) @name.reference.import) @reference.import

(import_spec
  path: (interpreted_string_literal
    (interpreted_string_literal_content) @name.reference.import)) @reference.import

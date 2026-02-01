(class_declaration
  name: (identifier) @name.definition.class) @definition.class

(function_declaration
  name: (identifier) @name.definition.function) @definition.function

(method_definition
  name: (property_identifier) @name.definition.method) @definition.method

(variable_declarator
  name: (identifier) @name.definition.variable
  value: [(arrow_function) (function)]) @definition.function

(call_expression
  function: [
    (identifier) @name.reference.call
    (member_expression
      property: (property_identifier) @name.reference.call)
  ]) @reference.call

(new_expression
  constructor: (identifier) @name.reference.class) @reference.class

(import_statement
  import_clause: (identifier) @name.reference.import) @reference.import

(import_specifier
  name: (identifier) @name.reference.import) @reference.import

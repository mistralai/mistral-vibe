; Odin symbol extraction

(procedure_declaration
  name: (identifier) @name.definition.function) @definition.function

(struct_declaration
  name: (identifier) @name.definition.class) @definition.class

(enum_declaration
  name: (identifier) @name.definition.class) @definition.class

(union_declaration
  name: (identifier) @name.definition.class) @definition.class

(const_declaration
  (identifier) @name.definition.property) @definition.property

(var_declaration
  (identifier) @name.definition.property) @definition.property

(call_expression
  function: (identifier) @name.reference.call) @reference.call

(call_expression
  function: (selector_expression
    field: (identifier) @name.reference.call)) @reference.call

(import_declaration
  (string_literal) @name.reference.import) @reference.import

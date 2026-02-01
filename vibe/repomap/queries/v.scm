; V language symbol extraction

(function_declaration
  name: (identifier) @name.definition.function) @definition.function

(struct_declaration
  name: (identifier) @name.definition.class) @definition.class

(interface_declaration
  name: (identifier) @name.definition.interface) @definition.interface

(enum_declaration
  name: (identifier) @name.definition.class) @definition.class

(type_declaration
  name: (identifier) @name.definition.type) @definition.type

(const_declaration
  (const_definition
    name: (identifier) @name.definition.property)) @definition.property

(global_var_declaration
  (global_var_definition
    name: (identifier) @name.definition.property)) @definition.property

(call_expression
  function: (identifier) @name.reference.call) @reference.call

(call_expression
  function: (selector_expression
    field: (identifier) @name.reference.call)) @reference.call

(import_declaration
  path: (import_path) @name.reference.import) @reference.import

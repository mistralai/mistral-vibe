; Scala symbol extraction

(class_definition
  name: (identifier) @name.definition.class) @definition.class

(object_definition
  name: (identifier) @name.definition.class) @definition.class

(trait_definition
  name: (identifier) @name.definition.interface) @definition.interface

(function_definition
  name: (identifier) @name.definition.function) @definition.function

(val_definition
  pattern: (identifier) @name.definition.property) @definition.property

(var_definition
  pattern: (identifier) @name.definition.property) @definition.property

(type_definition
  name: (type_identifier) @name.definition.type) @definition.type

(call_expression
  function: (identifier) @name.reference.call) @reference.call

(call_expression
  function: (field_expression
    field: (identifier) @name.reference.call)) @reference.call

(import_declaration
  path: (identifier) @name.reference.import) @reference.import

(extends_clause
  (type_identifier) @name.reference.inherit) @reference.inherit

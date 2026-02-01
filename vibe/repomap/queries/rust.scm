; Rust symbol extraction

(function_item
  name: (identifier) @name.definition.function) @definition.function

(struct_item
  name: (type_identifier) @name.definition.class) @definition.class

(enum_item
  name: (type_identifier) @name.definition.class) @definition.class

(trait_item
  name: (type_identifier) @name.definition.interface) @definition.interface

(impl_item
  trait: (type_identifier) @name.reference.inherit
  type: (type_identifier) @name.definition.class) @definition.class

(impl_item
  type: (type_identifier) @name.definition.class) @definition.class

(type_item
  name: (type_identifier) @name.definition.type) @definition.type

(mod_item
  name: (identifier) @name.definition.module) @definition.module

(const_item
  name: (identifier) @name.definition.constant) @definition.constant

(static_item
  name: (identifier) @name.definition.constant) @definition.constant

(macro_definition
  name: (identifier) @name.definition.macro) @definition.macro

(call_expression
  function: (identifier) @name.reference.call) @reference.call

(call_expression
  function: (field_expression
    field: (field_identifier) @name.reference.call)) @reference.call

(call_expression
  function: (scoped_identifier
    name: (identifier) @name.reference.call)) @reference.call

(macro_invocation
  macro: (identifier) @name.reference.call) @reference.call

(use_declaration
  argument: (scoped_identifier) @name.reference.import) @reference.import

(use_declaration
  argument: (identifier) @name.reference.import) @reference.import

(use_declaration
  argument: (use_as_clause
    path: (scoped_identifier) @name.reference.import)) @reference.import

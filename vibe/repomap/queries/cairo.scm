; Cairo (StarkNet) symbol extraction

(function_definition
  name: (identifier) @name.definition.function) @definition.function

(trait_definition
  name: (identifier) @name.definition.interface) @definition.interface

(impl_definition
  trait: (type_identifier) @name.reference.inherit) @reference.inherit

(struct_definition
  name: (identifier) @name.definition.class) @definition.class

(enum_definition
  name: (identifier) @name.definition.class) @definition.class

(type_alias_definition
  name: (identifier) @name.definition.type) @definition.type

(const_definition
  name: (identifier) @name.definition.property) @definition.property

(call_expression
  function: (identifier) @name.reference.call) @reference.call

(call_expression
  function: (scoped_identifier
    name: (identifier) @name.reference.call)) @reference.call

(use_declaration
  (scoped_identifier
    name: (identifier) @name.reference.import)) @reference.import

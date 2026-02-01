; Solidity symbol extraction

(contract_declaration
  name: (identifier) @name.definition.class) @definition.class

(interface_declaration
  name: (identifier) @name.definition.interface) @definition.interface

(library_declaration
  name: (identifier) @name.definition.class) @definition.class

(struct_declaration
  name: (identifier) @name.definition.class) @definition.class

(enum_declaration
  name: (identifier) @name.definition.class) @definition.class

(function_definition
  name: (identifier) @name.definition.function) @definition.function

(constructor_definition) @definition.function

(modifier_definition
  name: (identifier) @name.definition.function) @definition.function

(event_definition
  name: (identifier) @name.definition.function) @definition.function

(state_variable_declaration
  name: (identifier) @name.definition.property) @definition.property

(call_expression
  function: (identifier) @name.reference.call) @reference.call

(call_expression
  function: (member_expression
    property: (identifier) @name.reference.call)) @reference.call

(import_directive
  source: (string) @name.reference.import) @reference.import

(inheritance_specifier
  (user_defined_type
    (identifier) @name.reference.inherit)) @reference.inherit

; Gleam symbol extraction

(function
  name: (identifier) @name.definition.function) @definition.function

(type_definition
  name: (type_name) @name.definition.class) @definition.class

(type_alias
  name: (type_name) @name.definition.type) @definition.type

(constant
  name: (identifier) @name.definition.property) @definition.property

(external_function
  name: (identifier) @name.definition.function) @definition.function

(external_type
  name: (type_name) @name.definition.class) @definition.class

(function_call
  function: (identifier) @name.reference.call) @reference.call

(function_call
  function: (field_access
    label: (label) @name.reference.call)) @reference.call

(import
  module: (module) @name.reference.import) @reference.import

(unqualified_import
  name: (identifier) @name.reference.import) @reference.import

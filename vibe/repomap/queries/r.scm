; R symbol extraction

(left_assignment
  name: (identifier) @name.definition.property
  value: (function_definition)) @definition.function

(equals_assignment
  name: (identifier) @name.definition.property
  value: (function_definition)) @definition.function

(left_assignment
  name: (identifier) @name.definition.property) @definition.property

(equals_assignment
  name: (identifier) @name.definition.property) @definition.property

(call
  function: (identifier) @name.reference.call) @reference.call

(call
  function: (namespace_operator
    rhs: (identifier) @name.reference.call)) @reference.call

(call
  function: (identifier) @_keyword
  arguments: (arguments
    (argument
      value: (string) @name.reference.import))
  (#match? @_keyword "^(library|require)$")) @reference.import

; Lua symbol extraction

(function_declaration
  name: (identifier) @name.definition.function) @definition.function

(function_declaration
  name: (dot_index_expression
    field: (identifier) @name.definition.function)) @definition.function

(function_declaration
  name: (method_index_expression
    method: (identifier) @name.definition.function)) @definition.function

(local_function_declaration
  name: (identifier) @name.definition.function) @definition.function

(variable_declaration
  (assignment_statement
    (variable_list
      name: (identifier) @name.definition.property))) @definition.property

(function_call
  name: (identifier) @name.reference.call) @reference.call

(function_call
  name: (dot_index_expression
    field: (identifier) @name.reference.call)) @reference.call

(function_call
  name: (method_index_expression
    method: (identifier) @name.reference.call)) @reference.call

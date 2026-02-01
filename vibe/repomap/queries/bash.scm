; Bash/Shell symbol extraction

(function_definition
  name: (word) @name.definition.function) @definition.function

(command
  name: (command_name) @name.reference.call) @reference.call

(command
  name: (command_name
    (word) @name.reference.call)) @reference.call

(variable_assignment
  name: (variable_name) @name.definition.property) @definition.property

(simple_expansion
  (variable_name) @name.reference.variable) @reference.variable

(expansion
  (variable_name) @name.reference.variable) @reference.variable

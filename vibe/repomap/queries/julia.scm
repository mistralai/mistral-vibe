; Julia symbol extraction

(function_definition
  name: (identifier) @name.definition.function) @definition.function

(short_function_definition
  name: (identifier) @name.definition.function) @definition.function

(struct_definition
  name: (identifier) @name.definition.class) @definition.class

(abstract_definition
  name: (identifier) @name.definition.interface) @definition.interface

(module_definition
  name: (identifier) @name.definition.class) @definition.class

(const_statement
  (assignment
    (identifier) @name.definition.property)) @definition.property

(assignment
  (identifier) @name.definition.property) @definition.property

(call_expression
  (identifier) @name.reference.call) @reference.call

(call_expression
  (field_expression
    (identifier) @name.reference.call)) @reference.call

(import_statement
  (identifier) @name.reference.import) @reference.import

(using_statement
  (identifier) @name.reference.import) @reference.import

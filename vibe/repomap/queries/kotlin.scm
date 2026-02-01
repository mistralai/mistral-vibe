; Kotlin symbol extraction

(class_declaration
  (type_identifier) @name.definition.class) @definition.class

(object_declaration
  (type_identifier) @name.definition.class) @definition.class

(interface_declaration
  (type_identifier) @name.definition.interface) @definition.interface

(function_declaration
  (simple_identifier) @name.definition.function) @definition.function

(property_declaration
  (variable_declaration
    (simple_identifier) @name.definition.property)) @definition.property

(call_expression
  (simple_identifier) @name.reference.call) @reference.call

(call_expression
  (navigation_expression
    (simple_identifier) @name.reference.call)) @reference.call

(import_header
  (identifier) @name.reference.import) @reference.import

(delegation_specifier
  (user_type
    (type_identifier) @name.reference.inherit)) @reference.inherit

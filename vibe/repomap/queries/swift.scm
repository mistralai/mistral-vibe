; Swift symbol extraction

(class_declaration
  name: (type_identifier) @name.definition.class) @definition.class

(struct_declaration
  name: (type_identifier) @name.definition.class) @definition.class

(protocol_declaration
  name: (type_identifier) @name.definition.interface) @definition.interface

(enum_declaration
  name: (type_identifier) @name.definition.class) @definition.class

(function_declaration
  name: (simple_identifier) @name.definition.function) @definition.function

(init_declaration) @definition.function

(property_declaration
  (pattern
    (simple_identifier) @name.definition.property)) @definition.property

(call_expression
  (simple_identifier) @name.reference.call) @reference.call

(call_expression
  (navigation_expression
    (simple_identifier) @name.reference.call)) @reference.call

(import_declaration
  (identifier) @name.reference.import) @reference.import

(inheritance_specifier
  (user_type
    (type_identifier) @name.reference.inherit)) @reference.inherit

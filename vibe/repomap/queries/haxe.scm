; Haxe symbol extraction

(class_declaration
  name: (identifier) @name.definition.class) @definition.class

(interface_declaration
  name: (identifier) @name.definition.interface) @definition.interface

(enum_declaration
  name: (identifier) @name.definition.class) @definition.class

(abstract_declaration
  name: (identifier) @name.definition.class) @definition.class

(typedef_declaration
  name: (identifier) @name.definition.type) @definition.type

(function_declaration
  name: (identifier) @name.definition.function) @definition.function

(var_declaration
  name: (identifier) @name.definition.property) @definition.property

(call_expression
  function: (identifier) @name.reference.call) @reference.call

(call_expression
  function: (field_access
    field: (identifier) @name.reference.call)) @reference.call

(import_declaration
  path: (path) @name.reference.import) @reference.import

(extends_declaration
  (type_path
    (identifier) @name.reference.inherit)) @reference.inherit

(implements_declaration
  (type_path
    (identifier) @name.reference.inherit)) @reference.inherit

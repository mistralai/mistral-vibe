; GDScript (Godot) symbol extraction

(class_definition
  name: (name) @name.definition.class) @definition.class

(class_name_statement
  (name) @name.definition.class) @definition.class

(function_definition
  name: (name) @name.definition.function) @definition.function

(signal_statement
  name: (name) @name.definition.function) @definition.function

(variable_statement
  name: (name) @name.definition.property) @definition.property

(const_statement
  name: (name) @name.definition.property) @definition.property

(enum_definition
  name: (name) @name.definition.class) @definition.class

(call
  (identifier) @name.reference.call) @reference.call

(call
  (attribute
    attribute: (identifier) @name.reference.call)) @reference.call

(extends_statement
  (identifier) @name.reference.inherit) @reference.inherit

(preload_statement
  (string) @name.reference.import) @reference.import

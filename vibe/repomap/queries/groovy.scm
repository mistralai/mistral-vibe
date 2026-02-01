; Groovy symbol extraction

(class_declaration
  name: (identifier) @name.definition.class) @definition.class

(interface_declaration
  name: (identifier) @name.definition.interface) @definition.interface

(enum_declaration
  name: (identifier) @name.definition.class) @definition.class

(trait_declaration
  name: (identifier) @name.definition.interface) @definition.interface

(method_declaration
  name: (identifier) @name.definition.function) @definition.function

(constructor_declaration
  name: (identifier) @name.definition.function) @definition.function

(variable_declarator
  name: (identifier) @name.definition.property) @definition.property

(method_invocation
  method: (identifier) @name.reference.call) @reference.call

(import_declaration
  (identifier) @name.reference.import) @reference.import

(superclass
  (type_identifier) @name.reference.inherit) @reference.inherit

(super_interfaces
  (type_list
    (type_identifier) @name.reference.inherit)) @reference.inherit

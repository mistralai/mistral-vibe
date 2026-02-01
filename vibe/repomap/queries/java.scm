; Java symbol extraction

(class_declaration
  name: (identifier) @name.definition.class) @definition.class

(interface_declaration
  name: (identifier) @name.definition.interface) @definition.interface

(enum_declaration
  name: (identifier) @name.definition.class) @definition.class

(record_declaration
  name: (identifier) @name.definition.class) @definition.class

(annotation_type_declaration
  name: (identifier) @name.definition.class) @definition.class

(method_declaration
  name: (identifier) @name.definition.function) @definition.function

(constructor_declaration
  name: (identifier) @name.definition.function) @definition.function

(method_invocation
  name: (identifier) @name.reference.call) @reference.call

(object_creation_expression
  type: (type_identifier) @name.reference.call) @reference.call

(import_declaration
  (scoped_identifier) @name.reference.import) @reference.import

(import_declaration
  (identifier) @name.reference.import) @reference.import

(package_declaration
  (scoped_identifier) @name.reference.import) @reference.import

(superclass
  (type_identifier) @name.reference.inherit) @reference.inherit

(super_interfaces
  (type_list
    (type_identifier) @name.reference.inherit)) @reference.inherit

(extends_interfaces
  (type_list
    (type_identifier) @name.reference.inherit)) @reference.inherit
